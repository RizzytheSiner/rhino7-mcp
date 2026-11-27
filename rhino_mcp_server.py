"""
Rhino 7 MCP Server

Claude 데스크톱 앱이 이 서버를 실행하고, 이 서버는 Rhino 안에서 돌고 있는
rhino_listener.py 와 로컬 소켓(기본 127.0.0.1:7311)으로 통신합니다.

필요: Python 3.10+, `pip install mcp` (1.x, 2.x 모두 지원)
환경 변수(선택): RHINO_MCP_HOST, RHINO_MCP_PORT, RHINO_MCP_TIMEOUT
"""
import base64
import itertools
import json
import os
import socket
from typing import Any, Literal, Optional, Union

try:  # mcp 2.x
    from mcp.server.mcpserver import Image, MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP, Image
    from mcp.server.fastmcp.exceptions import ToolError

HOST = os.environ.get("RHINO_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("RHINO_MCP_PORT", "7311"))
TIMEOUT = float(os.environ.get("RHINO_MCP_TIMEOUT", "130"))

mcp = FastMCP(
    "rhino7",
    instructions=(
        "Tools for reading and editing the model open in Rhino 7. "
        "Units follow the Rhino document (check get_document_info first). "
        "Coordinates are [x, y, z] lists; colors are [r, g, b] 0-255. "
        "Layers can be nested with 'Parent::Child'. "
        "Prefer the dedicated tools; use execute_python for anything they don't cover. "
        "After changing geometry, capture_viewport is a good way to check the result. "
        "Every change is one undo step in Rhino (Ctrl+Z)."
    ),
)

_ids = itertools.count(1)


class RhinoError(ToolError):
    """ToolError 를 상속해야 오류 내용이 Claude 에게 그대로 전달됩니다."""


def send(command: str, params: Optional[dict] = None) -> Any:
    """리스너에 JSON 한 줄을 보내고 응답 한 줄을 받습니다."""
    req = {"id": next(_ids), "command": command, "params": params or {}}
    try:
        sock = socket.create_connection((HOST, PORT), timeout=5)
    except OSError:
        raise RhinoError(
            f"Rhino({HOST}:{PORT})에 연결할 수 없습니다. Rhino 7이 열려 있고 "
            "rhino_listener.py 를 RunPythonScript 로 실행했는지 확인하세요."
        )
    with sock:
        sock.settimeout(TIMEOUT)
        sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        except socket.timeout:
            raise RhinoError("Rhino 응답 시간 초과")
    line = buf.split(b"\n", 1)[0].decode("utf-8").strip()
    if not line:
        raise RhinoError("Rhino에서 빈 응답이 왔습니다.")
    resp = json.loads(line)
    if not resp.get("ok"):
        msg = resp.get("error", "알 수 없는 오류")
        if resp.get("trace"):
            msg += "\n" + resp["trace"]
        raise RhinoError(msg)
    return resp.get("result")


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


# --------------------------------------------------------------------- 읽기
@mcp.tool()
def get_document_info() -> dict:
    """Overview of the open Rhino document: file name/path, units, tolerance,
    object count, overall bounding box, all layers (with color, visibility,
    lock state, object counts) and viewport names."""
    return send("get_document_info")


@mcp.tool()
def list_objects(
    layer: Optional[str] = None,
    selected_only: bool = False,
    object_type: Optional[str] = None,
    limit: int = 200,
) -> dict:
    """List objects with id, type, layer, name and bounding box.

    layer: full layer path; includes sublayers.
    object_type: substring filter such as 'curve', 'brep', 'mesh', 'point', 'extrusion'.
    limit: max objects returned (<= 500)."""
    return send("list_objects", _clean({
        "layer": layer, "selected_only": selected_only,
        "object_type": object_type, "limit": limit,
    }))


@mcp.tool()
def get_object_info(object_id: str) -> dict:
    """Detailed info for one object: attributes, color, user text, and geometry
    data (curve length/closed/area, brep or mesh area/volume)."""
    return send("get_object_info", {"id": object_id})


# --------------------------------------------------------------- 생성·편집
@mcp.tool()
def create_object(
    object_type: Literal[
        "point", "line", "polyline", "circle", "rectangle",
        "interp_curve", "box", "cylinder", "sphere",
    ],
    geometry: dict,
    layer: Optional[str] = None,
    name: Optional[str] = None,
    color: Optional[list[int]] = None,
) -> dict:
    """Create one object. `geometry` keys by type:
    - point: {point}
    - line: {start, end}
    - polyline: {points, closed?}
    - circle: {center, radius, normal? = [0,0,1]}
    - rectangle: {corner, width, height}  (on a plane parallel to world XY)
    - interp_curve: {points, degree? = 3}
    - box: {corner, size: [dx, dy, dz]}
    - cylinder: {base, radius, height, axis? = [0,0,1]}
    - sphere: {center, radius}
    The layer is created if it does not exist. Returns the new object's summary."""
    params = dict(geometry)
    params.update(_clean({"type": object_type, "layer": layer,
                          "name": name, "color": color}))
    return send("create_object", params)


@mcp.tool()
def modify_objects(
    object_ids: list[str],
    translate: Optional[list[float]] = None,
    rotate_degrees: Optional[float] = None,
    rotate_axis: Optional[list[float]] = None,
    rotate_center: Optional[list[float]] = None,
    scale: Optional[Union[float, list[float]]] = None,
    scale_origin: Optional[list[float]] = None,
    copy: bool = False,
    layer: Optional[str] = None,
    name: Optional[str] = None,
    color: Optional[list[int]] = None,
) -> dict:
    """Transform and/or change attributes of objects.
    Transform order: scale (uniform number or [sx, sy, sz]) -> rotate -> translate.
    rotate_axis defaults to [0,0,1], centers/origins default to [0,0,0].
    copy=True keeps the originals and transforms copies.
    Returns summaries of the resulting objects."""
    params: dict = _clean({
        "ids": object_ids, "translate": translate, "scale": scale,
        "scale_origin": scale_origin, "copy": copy,
        "layer": layer, "name": name, "color": color,
    })
    if rotate_degrees:
        params["rotate"] = _clean({"angle": rotate_degrees, "axis": rotate_axis,
                                   "center": rotate_center})
    return send("modify_objects", params)


@mcp.tool()
def delete_objects(object_ids: list[str]) -> dict:
    """Delete objects by id (undoable in Rhino)."""
    return send("delete_objects", {"ids": object_ids})


@mcp.tool()
def create_layer(name: str, color: Optional[list[int]] = None,
                 set_current: bool = False) -> dict:
    """Create a layer (use 'Parent::Child' for nesting) or update its color
    if it exists. set_current makes it the current layer."""
    return send("create_layer", _clean({"name": name, "color": color,
                                        "set_current": set_current}))


@mcp.tool()
def select_objects(object_ids: list[str], clear_existing: bool = True) -> dict:
    """Select objects in Rhino so the user can see or act on them."""
    return send("select_objects", {"ids": object_ids, "clear": clear_existing})


# ------------------------------------------------------------ Python 실행
@mcp.tool()
def execute_python(code: str) -> dict:
    """Run IronPython 2.7 code inside Rhino 7 (NOT Python 3: no f-strings,
    use print() and % formatting).
    Pre-imported names: rs (rhinoscriptsyntax), sc (scriptcontext), Rhino,
    G (Rhino.Geometry), System, doc (active RhinoDoc), math.
    Printed output is returned as stdout; assign to a variable named `result`
    to return a value. The whole run is a single undo step."""
    return send("execute_python", {"code": code})


# --------------------------------------------------------------- 뷰 캡처
@mcp.tool()
def capture_viewport(
    view: Optional[str] = None,
    width: int = 800,
    height: int = 600,
    display_mode: Optional[str] = None,
    zoom_extents: bool = False,
) -> Image:
    """Capture a Rhino viewport as a PNG image.
    view: viewport name (e.g. 'Perspective', 'Top'); default is the active view.
    display_mode: e.g. 'Shaded', 'Rendered', 'Wireframe', 'Arctic' (changes the view).
    zoom_extents: zoom to all objects before capturing."""
    res = send("capture_viewport", _clean({
        "view": view, "width": width, "height": height,
        "display_mode": display_mode, "zoom_extents": zoom_extents,
    }))
    return Image(data=base64.b64decode(res["png_base64"]), format="png")


if __name__ == "__main__":
    mcp.run()
