// rhino7-mcp setup: Rhino 7 <-> Claude Desktop MCP installer for Windows.
//
// What it does (Python and Rhino 7 are assumed to be installed):
//  1. Copies the MCP files to %USERPROFILE%\rhino7mcp
//  2. Finds a suitable Python (3.10-3.13 preferred) and installs the `mcp` package
//  3. Registers the "rhino7" server in the Claude Desktop config (keeps other settings)
//  4. Creates a desktop shortcut "Rhino 7 + Claude" that starts Rhino with the listener
//  5. Optionally starts Rhino and checks the connection
package main

import (
	"bufio"
	"embed"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

//go:embed payload/*
var payload embed.FS

const version = "1.0.0"
const port = 7311

var logw io.Writer = os.Stdout
var stdin = bufio.NewReader(os.Stdin)

func say(format string, a ...any) { fmt.Fprintf(logw, format+"\n", a...) }
func step(n int, title string)     { say("\n[%d/6] %s", n, title) }
func ok(format string, a ...any)    { say("   ✔ "+format, a...) }
func warn(format string, a ...any)  { say("   ! "+format, a...) }

func fail(format string, a ...any) {
	say("\n   ✖ "+format, a...)
	say("\n설치를 끝내지 못했습니다. 위 메시지와 install_log.txt 를 확인해 주세요.")
	pause()
	os.Exit(1)
}

func pause() {
	fmt.Print("\nEnter 를 누르면 창이 닫힙니다...")
	stdin.ReadString('\n')
}

func ask(q string, def bool) bool {
	d := "Y/n"
	if !def {
		d = "y/N"
	}
	fmt.Printf("\n   %s [%s] ", q, d)
	s, _ := stdin.ReadString('\n')
	s = strings.ToLower(strings.TrimSpace(s))
	if s == "" {
		return def
	}
	return s == "y" || s == "yes" || s == "ㅛ"
}

func pyEnv() []string {
	return append(os.Environ(), "PYTHONIOENCODING=utf-8", "PYTHONUTF8=1", "PIP_DISABLE_PIP_VERSION_CHECK=1")
}

// run executes a command, logs its output and returns it.
func run(dir string, name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	cmd.Dir = dir
	cmd.Env = pyEnv()
	out, err := cmd.CombinedOutput()
	if f, ok := logw.(*multi); ok {
		f.file.Write(out) // full output goes to the log file only
	}
	return string(out), err
}

type multi struct {
	file *os.File
}

func (m *multi) Write(p []byte) (int, error) {
	os.Stdout.Write(p)
	return m.file.Write(p)
}

// ---------------------------------------------------------------- python

type pyInfo struct {
	exe          string
	major, minor int
}

func probe(name string, pre ...string) (pyInfo, bool) {
	args := append(pre, "-c", "import sys;print(sys.executable);print(sys.version_info[0]);print(sys.version_info[1])")
	cmd := exec.Command(name, args...)
	cmd.Env = pyEnv()
	out, err := cmd.Output()
	if err != nil {
		return pyInfo{}, false
	}
	lines := strings.Fields(strings.TrimSpace(string(out)))
	if len(lines) < 3 {
		return pyInfo{}, false
	}
	// executable path may contain spaces; version numbers are the last two fields
	maj, e1 := strconv.Atoi(lines[len(lines)-2])
	min, e2 := strconv.Atoi(lines[len(lines)-1])
	exe := strings.TrimSpace(strings.SplitN(strings.TrimSpace(string(out)), "\n", 2)[0])
	if e1 != nil || e2 != nil || exe == "" {
		return pyInfo{}, false
	}
	return pyInfo{exe, maj, min}, true
}

func findPythons() []pyInfo {
	seen := map[string]bool{}
	var found []pyInfo
	add := func(p pyInfo, okk bool) {
		if okk && !seen[strings.ToLower(p.exe)] && p.major == 3 && p.minor >= 10 {
			seen[strings.ToLower(p.exe)] = true
			found = append(found, p)
		}
	}
	for _, v := range []string{"3.13", "3.12", "3.11", "3.10"} {
		add(probe("py", "-"+v))
	}
	la := os.Getenv("LOCALAPPDATA")
	for _, v := range []string{"313", "312", "311", "310", "314", "315"} {
		for _, p := range []string{
			filepath.Join(la, "Programs", "Python", "Python"+v, "python.exe"),
			filepath.Join(la, "Python", "pythoncore-3."+v[1:]+"-64", "python.exe"),
			filepath.Join(`C:\Program Files`, "Python"+v, "python.exe"),
		} {
			if _, err := os.Stat(p); err == nil {
				add(probe(p))
			}
		}
	}
	add(probe("python"))
	add(probe("python3"))
	add(probe("py"))
	return found
}

func choosePython(list []pyInfo) (pyInfo, bool) {
	if len(list) == 0 {
		return pyInfo{}, false
	}
	sort.SliceStable(list, func(i, j int) bool {
		gi := list[i].minor <= 13
		gj := list[j].minor <= 13
		if gi != gj {
			return gi // 3.10-3.13 first
		}
		return list[i].minor > list[j].minor
	})
	return list[0], true
}

func installMcp(py pyInfo) bool {
	if _, err := run("", py.exe, "-m", "pip", "--version"); err != nil {
		run("", py.exe, "-m", "ensurepip", "--upgrade")
	}
	say("   mcp 패키지를 설치하는 중... (1~2분 걸릴 수 있습니다)")
	run("", py.exe, "-m", "pip", "install", "--upgrade", "--user", "mcp")
	out, err := run("", py.exe, "-c", "import mcp; print('mcp ok')")
	if err == nil && strings.Contains(out, "mcp ok") {
		return true
	}
	// retry without --user (virtualenv / system installs)
	run("", py.exe, "-m", "pip", "install", "--upgrade", "mcp")
	out, err = run("", py.exe, "-c", "import mcp; print('mcp ok')")
	return err == nil && strings.Contains(out, "mcp ok")
}

// ---------------------------------------------------------------- helpers

func shortPath(p string) string {
	k := syscall.NewLazyDLL("kernel32.dll").NewProc("GetShortPathNameW")
	in, err := syscall.UTF16PtrFromString(p)
	if err != nil {
		return p
	}
	buf := make([]uint16, 1024)
	n, _, _ := k.Call(uintptr(unsafe.Pointer(in)), uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
	if n == 0 || int(n) > len(buf) {
		return p
	}
	return syscall.UTF16ToString(buf[:n])
}

func copyPayload(dst string) error {
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return err
	}
	entries, err := payload.ReadDir("payload")
	if err != nil {
		return err
	}
	for _, e := range entries {
		b, err := payload.ReadFile("payload/" + e.Name())
		if err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(dst, e.Name()), b, 0o644); err != nil {
			return err
		}
	}
	return nil
}

func findRhino() string {
	out, err := exec.Command("reg", "query", `HKLM\SOFTWARE\McNeel\Rhinoceros\7.0\Install`, "/v", "InstallPath").Output()
	if err == nil {
		re := regexp.MustCompile(`InstallPath\s+REG_SZ\s+(.+)`)
		if m := re.FindStringSubmatch(string(out)); m != nil {
			p := filepath.Join(strings.TrimSpace(m[1]), "System", "Rhino.exe")
			if _, err := os.Stat(p); err == nil {
				return p
			}
		}
	}
	for _, p := range []string{`C:\Program Files\Rhino 7\System\Rhino.exe`, `C:\Program Files\Rhinoceros 7\System\Rhino.exe`} {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return ""
}

func processRunning(image string) bool {
	out, _ := exec.Command("tasklist", "/FI", "IMAGENAME eq "+image, "/NH").Output()
	return strings.Contains(strings.ToLower(string(out)), strings.ToLower(image))
}

func createShortcut(target, args, workdir string) (string, error) {
	ps := `$d=[Environment]::GetFolderPath('Desktop');` +
		`$p=Join-Path $d 'Rhino 7 + Claude.lnk';` +
		`$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);` +
		`$s.TargetPath=$env:R7_TARGET;$s.Arguments=$env:R7_ARGS;$s.WorkingDirectory=$env:R7_DIR;` +
		`$s.IconLocation=$env:R7_TARGET+',0';$s.Description='Rhino 7 with Claude MCP listener';$s.Save();Write-Output $p`
	cmd := exec.Command("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps)
	cmd.Env = append(os.Environ(), "R7_TARGET="+target, "R7_ARGS="+args, "R7_DIR="+workdir)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// pingRhino sends one request to the listener and returns the parsed result.
func pingRhino(timeout time.Duration) (map[string]any, error) {
	c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 2*time.Second)
	if err != nil {
		return nil, err
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(timeout))
	if _, err := c.Write([]byte(`{"id":1,"command":"ping","params":{}}` + "\n")); err != nil {
		return nil, err
	}
	line, err := bufio.NewReader(c).ReadString('\n')
	if err != nil {
		return nil, err
	}
	var resp map[string]any
	if err := json.Unmarshal([]byte(line), &resp); err != nil {
		return nil, err
	}
	if okv, _ := resp["ok"].(bool); !okv {
		return nil, fmt.Errorf("%v", resp["error"])
	}
	res, _ := resp["result"].(map[string]any)
	return res, nil
}

// serverSelfTest starts the MCP server and checks that it answers "initialize".
func serverSelfTest(py, dir string) error {
	cmd := exec.Command(py, filepath.Join(dir, "rhino_mcp_server.py"))
	cmd.Dir = dir
	cmd.Env = pyEnv()
	in, _ := cmd.StdinPipe()
	outp, _ := cmd.StdoutPipe()
	cmd.Stderr = io.Discard
	if err := cmd.Start(); err != nil {
		return err
	}
	defer cmd.Process.Kill()
	in.Write([]byte(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"setup","version":"1"}}}` + "\n"))
	done := make(chan string, 1)
	go func() {
		l, _ := bufio.NewReader(outp).ReadString('\n')
		done <- l
	}()
	select {
	case l := <-done:
		if strings.Contains(l, `"rhino7"`) {
			return nil
		}
		return fmt.Errorf("unexpected response: %.200s", l)
	case <-time.After(30 * time.Second):
		return fmt.Errorf("no response within 30s")
	}
}

// ---------------------------------------------------------------- main

func main() {
	home := os.Getenv("USERPROFILE")
	dir := filepath.Join(home, "rhino7mcp")

	fmt.Printf("Rhino 7 ↔ Claude MCP 설치 프로그램 v%s\n", version)
	fmt.Println("=============================================")
	fmt.Println("필요한 것: Windows, Rhino 7, Python 3.10 이상, Claude 데스크톱 앱")

	// 1. files ---------------------------------------------------------------
	step(1, "파일 복사")
	if err := copyPayload(dir); err != nil {
		fail("파일을 복사하지 못했습니다: %v", err)
	}
	if strings.Contains(shortPath(dir), " ") { // Rhino /runscript needs a path without spaces
		alt := filepath.Join(os.Getenv("PUBLIC"), "rhino7mcp")
		if err := copyPayload(alt); err == nil {
			dir = alt
		}
	}
	if f, err := os.Create(filepath.Join(dir, "install_log.txt")); err == nil {
		defer f.Close()
		logw = &multi{f}
		fmt.Fprintf(f, "rhino7-mcp setup v%s  %s\n", version, time.Now().Format(time.RFC3339))
	}
	ok("%s", dir)

	// 2. python --------------------------------------------------------------
	step(2, "Python 확인")
	py, found := choosePython(findPythons())
	if !found {
		fail("Python 3.10 이상을 찾지 못했습니다. python.org 에서 Python 3.13 을 설치한 뒤 다시 실행해 주세요.")
	}
	ok("Python %d.%d  (%s)", py.major, py.minor, py.exe)
	if py.minor >= 14 {
		warn("Python 3.14 이상은 mcp 설치가 실패할 수 있습니다. 실패하면 3.13 설치를 시도합니다.")
	}

	// 3. mcp -----------------------------------------------------------------
	step(3, "mcp 패키지 설치")
	if !installMcp(py) {
		if py.minor >= 14 {
			warn("Python %d.%d 에서 설치 실패 → Python 3.13 을 추가로 설치합니다.", py.major, py.minor)
			run("", "py", "install", "-y", "3.13")
			if p, okk := probe("py", "-3.13"); okk {
				py = p
			} else {
				run("", "winget", "install", "--id", "Python.Python.3.13", "-e", "--scope", "user", "--silent",
					"--accept-package-agreements", "--accept-source-agreements")
				if p, okk := probe(filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "Python", "Python313", "python.exe")); okk {
					py = p
				}
			}
			if py.minor != 13 || !installMcp(py) {
				fail("mcp 를 설치하지 못했습니다. Python 3.13 을 설치한 뒤 다시 실행해 주세요.")
			}
		} else {
			fail("mcp 를 설치하지 못했습니다. 인터넷 연결을 확인하고 다시 실행해 주세요.")
		}
	}
	ok("mcp 설치 완료 (Python %d.%d)", py.major, py.minor)
	if err := serverSelfTest(py.exe, dir); err != nil {
		fail("MCP 서버 자체 테스트 실패: %v", err)
	}
	ok("MCP 서버 자체 테스트 통과")

	// 4. claude config -------------------------------------------------------
	step(4, "Claude 데스크톱 앱에 rhino7 등록")
	out, err := run(dir, py.exe, "merge_config.py", py.exe)
	if err != nil {
		fail("구성 파일을 수정하지 못했습니다:\n%s", out)
	}
	for _, l := range strings.Split(out, "\n") {
		if strings.HasPrefix(l, "--- written:") {
			ok("%s", strings.TrimSpace(strings.TrimPrefix(l, "--- written:")))
		}
		if strings.Contains(l, "recovering") {
			warn("기존 구성 파일 형식이 깨져 있어 서버 항목만 살려 다시 저장했습니다 (백업 .bak 생성).")
		}
	}

	// 5. rhino shortcut --------------------------------------------------------
	step(5, "Rhino 7 바로가기 만들기")
	rhino := findRhino()
	listener := shortPath(filepath.Join(dir, "rhino_listener.py"))
	rhinoArgs := fmt.Sprintf(`/nosplash /runscript="_-RunPythonScript %s"`, listener)
	if rhino == "" {
		warn("Rhino 7 을 찾지 못해 바로가기를 만들지 않았습니다.")
	} else if lnk, err := createShortcut(rhino, rhinoArgs, dir); err != nil {
		warn("바로가기를 만들지 못했습니다: %v %s", err, lnk)
	} else {
		ok("바탕화면: %s", lnk)
	}
	say("   다른 방법으로 Rhino 를 열 때도 리스너를 켜려면 Rhino 도구 > 옵션 > 일반 >")
	say("   'Rhino를 시작할 때마다 이 명령 실행' 칸에 아래 한 줄을 넣으세요:")
	say("     _-RunPythonScript %s", listener)

	// 6. connection --------------------------------------------------------------
	step(6, "Rhino 연결 확인")
	if res, err := pingRhino(10 * time.Second); err == nil {
		ok("이미 연결됨: Rhino %v", res["rhino"])
	} else if processRunning("Rhino.exe") {
		warn("Rhino 가 열려 있지만 리스너가 꺼져 있습니다. Rhino 명령행에 아래를 입력하세요:")
		say("     _-RunPythonScript %s", listener)
	} else if rhino != "" && ask("Rhino 7 을 지금 리스너와 함께 실행할까요?", true) {
		c := exec.Command(rhino, "/nosplash", fmt.Sprintf(`/runscript=_-RunPythonScript %s`, listener))
		c.SysProcAttr = &syscall.SysProcAttr{CmdLine: fmt.Sprintf(`"%s" %s`, rhino, rhinoArgs)}
		if err := c.Start(); err != nil {
			warn("Rhino 를 실행하지 못했습니다: %v", err)
		} else {
			say("   Rhino 가 열리기를 기다리는 중... (최대 2분)")
			deadline := time.Now().Add(2 * time.Minute)
			for time.Now().Before(deadline) {
				if res, err := pingRhino(10 * time.Second); err == nil {
					ok("연결 성공: Rhino %v", res["rhino"])
					break
				}
				time.Sleep(3 * time.Second)
			}
			if _, err := pingRhino(5 * time.Second); err != nil {
				warn("아직 연결되지 않았습니다. Rhino 가 다 열린 뒤 명령행에 리스너 명령을 직접 입력해 보세요.")
			}
		}
	}

	say("\n=============================================")
	say("설치 완료!")
	if processRunning("Claude.exe") {
		say("▶ 마지막 단계: Claude 앱을 작업 표시줄 오른쪽 트레이 아이콘에서 '종료'한 뒤 다시 여세요.")
		say("  (창만 닫으면 안 됩니다. 앱이 시작할 때 설정을 읽습니다.)")
	} else {
		say("▶ 이제 Claude 앱을 여세요.")
	}
	say("▶ Rhino 는 바탕화면의 'Rhino 7 + Claude' 바로가기로 여세요.")
	say("▶ 확인: Claude 에게 \"Rhino에 지금 뭐가 있어?\" 라고 물어보세요.")
	pause()
}
