# rhino7-mcp

Claude 데스크톱 앱에서 대화로 **Rhino 7** 모델을 읽고, 만들고, 고치고, 뷰포트를 캡처할 수 있게 해 주는 MCP 서버입니다.

```
Claude 데스크톱 앱 ──(MCP, stdio)── rhino_mcp_server.py ──(127.0.0.1:7311)── rhino_listener.py (Rhino 7 안, IronPython 2.7)
```

## 빠른 설치 (추천)

**필요한 것:** Windows 10/11, Rhino 7, Python 3.10 이상, Claude 데스크톱 앱

1. [`dist/rhino7-mcp-v1.0.zip`](dist/rhino7-mcp-v1.0.zip) 을 내려받습니다.
2. zip 을 오른쪽 클릭 → 속성 → **차단 해제** 체크 후 압축을 풉니다.
3. `rhino7-mcp-setup.exe` 를 실행합니다. "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행**.
4. 설치가 끝나면 Claude 앱을 트레이 아이콘에서 **완전히 종료**한 뒤 다시 엽니다.
5. Rhino 는 바탕화면의 **"Rhino 7 + Claude"** 바로가기로 엽니다.

설치 프로그램은 파일 복사, `mcp` 설치, Claude 구성 등록, 바로가기 생성, 연결 확인을 자동으로 합니다. 자세한 내용은 zip 안의 `README.txt` 를 보세요. 설치 프로그램 소스는 [`installer/`](installer/) 에 있습니다. 빌드하려면 `installer/payload/` 에 `rhino_listener.py`, `rhino_mcp_server.py`, `test_connection.py`, `merge_config.py`, `check.bat` 를 복사한 뒤 `GOOS=windows GOARCH=amd64 go build -o rhino7-mcp-setup.exe .` 를 실행하세요.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `rhino_listener.py` | Rhino 7 안에서 실행하는 리스너. 요청을 UI 스레드에서 처리하고, 변경 작업은 Undo 1단계로 기록 |
| `rhino_mcp_server.py` | Claude 데스크톱 앱이 실행하는 MCP 서버 (Python 3.10~3.13, `mcp` 1.x/2.x 지원) |
| `test_connection.py` | Claude 없이 Rhino 연결만 확인 |
| `merge_config.py` | Claude 구성 파일에 `rhino7` 서버를 등록 (기존 설정 유지, 깨진 JSON 복구, 백업 생성) |
| `install.bat` | Windows 원클릭 설치: Python 3.13 확인·설치 → `mcp` 설치 → 구성 파일 등록 → 자체 테스트 |
| `check.bat` | 연결이 안 될 때 구성 파일·프로세스·로그를 `check_log.txt` 로 모아 주는 진단 도구 |

## 수동 설치 (Windows)

1. 이 저장소를 받아서 원하는 폴더에 둡니다. 예: `C:\Users\<사용자>\rhino7mcp`
2. Rhino 7을 열고 명령행에 아래를 입력해 리스너를 켭니다.
   ```
   _-RunPythonScript "C:\Users\<사용자>\rhino7mcp\rhino_listener.py"
   ```
   `[Rhino MCP] 127.0.0.1:7311 에서 Claude 연결을 기다리는 중입니다.` 가 나오면 성공입니다.
3. Claude 앱을 트레이 아이콘에서 **완전히 종료**한 뒤 `install.bat` 을 더블클릭합니다.
   결과는 같은 폴더의 `install_log.txt` 에 저장되고, 마지막에 `연결 성공` 이 나오면 됩니다.
4. Claude 앱을 다시 엽니다. 도구 목록에 `rhino7` 이 보이면 연결된 것입니다.

**리스너 자동 실행:** Rhino 도구 → 옵션 → 일반 → "Rhino를 시작할 때마다 이 명령 실행" 칸에 2단계의 명령을 넣어 두세요.

### 설치 관련 메모
- Python **3.14 이상은 `mcp` 의존 패키지가 설치되지 않을 수 있어** 3.13을 따로 설치해 씁니다. 기존 Python은 건드리지 않습니다.
- Microsoft Store 버전 Claude 앱은 구성 파일이 `%APPDATA%\Claude` 가 아니라
  `%LOCALAPPDATA%\Claude-Data\<패키지명>\claude_desktop_config.json` 에 있습니다. `merge_config.py` 가 자동으로 찾습니다.
- 구성 파일은 Claude 앱이 **시작할 때만** 읽으므로 수정 후 반드시 앱을 완전히 재시작해야 합니다.

## Claude가 쓸 수 있는 도구

| 도구 | 하는 일 |
|---|---|
| `get_document_info` | 단위, 레이어, 객체 수, 전체 범위, 뷰 목록 |
| `list_objects` | 객체 목록 (레이어·선택·종류로 거르기) |
| `get_object_info` | 객체 상세 (길이, 면적, 부피, 사용자 텍스트 등) |
| `create_object` | 점, 선, 폴리라인, 원, 사각형, 곡선, 박스, 원기둥, 구 |
| `modify_objects` | 이동·회전·축척(비균일 포함)·복사, 레이어·이름·색 변경 |
| `delete_objects` | 삭제 |
| `create_layer` | 레이어 생성 (`부모::자식` 가능) |
| `select_objects` | Rhino에서 객체 선택 |
| `execute_python` | Rhino 안에서 IronPython 2.7 코드 실행 |
| `capture_viewport` | 뷰포트를 PNG로 캡처해 Claude가 직접 확인 |

## 사용 예

- "지금 Rhino 파일에 뭐가 있는지 알려줘"
- "'매스' 레이어에 6×6m, 높이 9m 박스를 만들고 캡처해서 보여줘"
- "선택한 박스를 X 방향으로만 1.5배 늘려줘"

## 주의

- `execute_python` 은 Rhino 안에서 임의의 코드를 실행합니다. 리스너는 `127.0.0.1` 연결만 받지만, 승인 창이 뜨면 내용을 확인하세요.
- Rhino에서 명령이 입력을 기다리는 중이면 응답이 늦거나 시간 초과가 날 수 있습니다. `Esc` 로 명령을 끝내고 다시 시도하세요.
- 다른 포트를 쓰려면 `rhino_listener.py` 의 `PORT` 와 MCP 서버의 환경 변수 `RHINO_MCP_PORT` 를 같이 바꾸세요.

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| "Rhino에 연결할 수 없습니다" | Rhino가 켜져 있고 리스너 메시지가 나왔는지. `python test_connection.py` 로 확인 |
| Claude 앱에 `rhino7` 이 안 보임 | 앱을 트레이에서 완전히 종료 후 재시작. 그래도 안 되면 `check.bat` 실행 후 `check_log.txt` 확인 |
| "포트 7311 을 열 수 없습니다" | 다른 프로그램이 포트 사용 중. 포트 번호 변경 |
