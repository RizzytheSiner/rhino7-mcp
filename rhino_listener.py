# -*- coding: utf-8 -*-
"""
Rhino 7 MCP Listener (IronPython 2.7)

Rhino 7 명령행에서 RunPythonScript 로 이 파일을 실행하면
127.0.0.1:7311 에서 MCP 서버(rhino_mcp_server.py)의 요청을 기다립니다.
- 다시 실행하면 기존 리스너를 끄고 새로 시작합니다.
- 모든 Rhino 작업은 UI 스레드에서 실행되고, 변경 작업은 명령마다 Undo 1단계로 기록됩니다.
- 127.0.0.1(이 컴퓨터)에서만 접속을 받습니다.
"""
from __future__ import print_function

import clr
clr.AddReference("System.Drawing")

import json
import math
import sys
import traceback

import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System
import System.Drawing
from System import Action, Guid
from System.Collections.Generic import List
from System.IO import MemoryStream, StreamReader, StreamWriter
from System.Net import IPAddress
from System.Net.Sockets import (SocketOptionLevel, SocketOptionName,
                                TcpListener)
from System.Text import UTF8Encoding
from System.Threading import ManualResetEvent, Thread, ThreadStart

G = Rhino.Geometry

HOST = "127.0.0.1"
PORT = 7311
STICKY_KEY = "rhino_mcp_listener"
UI_TIMEOUT_MS = 120000
MAX_LIST = 500
MAX_STDOUT = 20000


# ---------------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------------
def _log(msg):
    Rhino.RhinoApp.WriteLine("[Rhino MCP] " + msg)


def _doc():
    doc = Rhino.RhinoDoc.ActiveDoc
    if doc is None:
        raise ValueError("열린 Rhino 문서가 없습니다.")
    sc.doc = doc
    return doc


def _pt(v, label="point"):
    if v is None:
        raise ValueError("'%s' 좌표가 필요합니다." % label)
    if len(v) == 2:
        return G.Point3d(float(v[0]), float(v[1]), 0.0)
    return G.Point3d(float(v[0]), float(v[1]), float(v[2]))


def _vec(v, label="vector"):
    if v is None:
        raise ValueError("'%s' 벡터가 필요합니다." % label)
    if len(v) == 2:
        return G.Vector3d(float(v[0]), float(v[1]), 0.0)
    return G.Vector3d(float(v[0]), float(v[1]), float(v[2]))


def _plist(p):
    return [round(p.X, 6), round(p.Y, 6), round(p.Z, 6)]


def _pts(values):
    pts = List[G.Point3d]()
    for v in values:
        pts.Add(_pt(v))
    return pts


def _guid(s):
    try:
        return Guid(str(s))
    except Exception:
        raise ValueError("잘못된 객체 ID: %s" % s)


def _get_obj(doc, s):
    obj = doc.Objects.FindId(_guid(s))
    if obj is None:
        raise ValueError("객체를 찾을 수 없습니다: %s" % s)
    return obj


def _bbox(geom):
    bb = geom.GetBoundingBox(True)
    if not bb.IsValid:
        return None
    return {"min": _plist(bb.Min), "max": _plist(bb.Max)}


def _rgb(c):
    return [int(c.R), int(c.G), int(c.B)]


def _to_color(v):
    return System.Drawing.Color.FromArgb(int(v[0]), int(v[1]), int(v[2]))


def _ensure_layer(doc, full_path, color=None):
    """'부모::자식' 형식의 레이어를 찾고, 없으면 상위부터 차례로 만듭니다."""
    idx = doc.Layers.FindByFullPath(full_path, -1)
    if idx >= 0:
        return idx
    parts = full_path.split("::")
    parent_id = Guid.Empty
    path = ""
    for i, part in enumerate(parts):
        path = part if i == 0 else path + "::" + part
        idx = doc.Layers.FindByFullPath(path, -1)
        if idx < 0:
            layer = Rhino.DocObjects.Layer()
            layer.Name = part
            if parent_id != Guid.Empty:
                layer.ParentLayerId = parent_id
            if color is not None and i == len(parts) - 1:
                layer.Color = _to_color(color)
            idx = doc.Layers.Add(layer)
            if idx < 0:
                raise ValueError("레이어를 만들 수 없습니다: %s" % path)
        parent_id = doc.Layers[idx].Id
    return idx


def _make_attrs(doc, layer=None, name=None, color=None):
    attrs = doc.CreateDefaultAttributes()
    if layer:
        attrs.LayerIndex = _ensure_layer(doc, layer)
    if name:
        attrs.Name = name
    if color:
        attrs.ObjectColor = _to_color(color)
        attrs.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
    return attrs


def _all_objects(doc):
    s = Rhino.DocObjects.ObjectEnumeratorSettings()
    s.NormalObjects = True
    s.LockedObjects = True
    s.HiddenObjects = True
    s.IncludeLights = False
    s.IncludeGrips = False
    return list(doc.Objects.GetObjectList(s))


def _summary(doc, obj):
    a = obj.Attributes
    return {
        "id": str(obj.Id),
        "type": str(obj.ObjectType),
        "geometry": obj.Geometry.GetType().Name,
        "layer": doc.Layers[a.LayerIndex].FullPath,
        "name": a.Name or "",
        "bbox": _bbox(obj.Geometry),
    }


def _jsonable(x):
    try:
        json.dumps(x)
        return x
    except Exception:
        pass
    if isinstance(x, (list, tuple)):
        return [_jsonable(i) for i in x]
    if isinstance(x, dict):
        return dict((str(k), _jsonable(v)) for k, v in x.items())
    if isinstance(x, (G.Point3d, G.Vector3d)):
        return [x.X, x.Y, x.Z]
    try:
        return [_jsonable(i) for i in x]  # .NET 컬렉션
    except Exception:
        return float(x) if isinstance(x, (System.Single, System.Double, System.Decimal)) else (int(x) if isinstance(x, (System.Byte, System.SByte, System.Int16, System.UInt16, System.UInt32, System.Int64, System.UInt64)) else str(x))


# ---------------------------------------------------------------------------
# 명령: 모델 정보 읽기
# ---------------------------------------------------------------------------
def cmd_get_document_info(doc, p):
    objs = _all_objects(doc)
    counts = {}
    total_bb = G.BoundingBox.Empty
    for o in objs:
        counts[o.Attributes.LayerIndex] = counts.get(o.Attributes.LayerIndex, 0) + 1
        total_bb.Union(o.Geometry.GetBoundingBox(True))
    layers = []
    for layer in doc.Layers:
        if layer.IsDeleted:
            continue
        layers.append({
            "name": layer.FullPath,
            "color": _rgb(layer.Color),
            "visible": layer.IsVisible,
            "locked": layer.IsLocked,
            "current": layer.Index == doc.Layers.CurrentLayerIndex,
            "object_count": counts.get(layer.Index, 0),
        })
    active = doc.Views.ActiveView
    return {
        "name": doc.Name,
        "path": doc.Path,
        "units": str(doc.ModelUnitSystem),
        "tolerance": doc.ModelAbsoluteTolerance,
        "object_count": len(objs),
        "selected_count": len([o for o in objs if o.IsSelected(False) > 0]),
        "bbox": ({"min": _plist(total_bb.Min), "max": _plist(total_bb.Max)}
                 if total_bb.IsValid else None),
        "layers": layers,
        "views": [v.ActiveViewport.Name for v in doc.Views],
        "active_view": active.ActiveViewport.Name if active else None,
    }


def cmd_list_objects(doc, p):
    layer = p.get("layer")
    selected_only = bool(p.get("selected_only"))
    otype = (p.get("object_type") or "").lower()
    limit = min(int(p.get("limit") or 200), MAX_LIST)
    found = []
    for o in _all_objects(doc):
        if selected_only and o.IsSelected(False) <= 0:
            continue
        lp = doc.Layers[o.Attributes.LayerIndex].FullPath
        if layer and not (lp == layer or lp.startswith(layer + "::")):
            continue
        if otype and otype not in str(o.ObjectType).lower() \
                and otype not in o.Geometry.GetType().Name.lower():
            continue
        found.append(o)
    return {
        "total": len(found),
        "returned": min(len(found), limit),
        "objects": [_summary(doc, o) for o in found[:limit]],
    }


def cmd_get_object_info(doc, p):
    obj = _get_obj(doc, p.get("id"))
    a = obj.Attributes
    info = _summary(doc, obj)
    info["color_source"] = str(a.ColorSource)
    info["display_color"] = _rgb(a.DrawColor(doc))
    info["visible"] = a.Visible
    info["selected"] = obj.IsSelected(False) > 0
    us = a.GetUserStrings()
    info["user_text"] = dict((k, us[k]) for k in us.AllKeys)

    g = obj.Geometry
    try:
        if isinstance(g, G.Point):
            info["location"] = _plist(g.Location)
        elif isinstance(g, G.Curve):
            c = {"length": g.GetLength(), "closed": g.IsClosed,
                 "planar": g.IsPlanar(), "degree": g.Degree,
                 "start": _plist(g.PointAtStart), "end": _plist(g.PointAtEnd)}
            if g.IsClosed and g.IsPlanar():
                amp = G.AreaMassProperties.Compute(g)
                if amp:
                    c["area"] = amp.Area
            info["curve"] = c
        elif isinstance(g, G.Mesh):
            m = {"vertices": g.Vertices.Count, "faces": g.Faces.Count,
                 "closed": g.IsClosed}
            amp = G.AreaMassProperties.Compute(g)
            if amp:
                m["area"] = amp.Area
            if g.IsClosed:
                vmp = G.VolumeMassProperties.Compute(g)
                if vmp:
                    m["volume"] = vmp.Volume
            info["mesh"] = m
        else:
            brep = None
            if isinstance(g, G.Brep):
                brep = g
            elif isinstance(g, (G.Extrusion, G.Surface)):
                brep = g.ToBrep()
            if brep is not None:
                b = {"faces": brep.Faces.Count, "solid": brep.IsSolid}
                amp = G.AreaMassProperties.Compute(brep)
                if amp:
                    b["area"] = amp.Area
                if brep.IsSolid:
                    vmp = G.VolumeMassProperties.Compute(brep)
                    if vmp:
                        b["volume"] = vmp.Volume
                info["brep"] = b
    except Exception as e:
        info["geometry_error"] = str(e)
    return info


# ---------------------------------------------------------------------------
# 명령: 형상 생성·편집
# ---------------------------------------------------------------------------
def cmd_create_object(doc, p):
    t = (p.get("type") or "").lower()
    attrs = _make_attrs(doc, p.get("layer"), p.get("name"), p.get("color"))
    gid = Guid.Empty

    if t == "point":
        gid = doc.Objects.AddPoint(_pt(p.get("point"), "point"), attrs)
    elif t == "line":
        gid = doc.Objects.AddLine(_pt(p.get("start"), "start"),
                                  _pt(p.get("end"), "end"), attrs)
    elif t == "polyline":
        pts = _pts(p.get("points") or [])
        if pts.Count < 2:
            raise ValueError("polyline 에는 점이 2개 이상 필요합니다.")
        if p.get("closed") and pts[0].DistanceTo(pts[pts.Count - 1]) > doc.ModelAbsoluteTolerance:
            pts.Add(pts[0])
        gid = doc.Objects.AddPolyline(pts, attrs)
    elif t == "circle":
        normal = _vec(p.get("normal") or [0, 0, 1])
        circle = G.Circle(G.Plane(_pt(p.get("center"), "center"), normal),
                          float(p.get("radius")))
        gid = doc.Objects.AddCircle(circle, attrs)
    elif t == "rectangle":
        plane = G.Plane(_pt(p.get("corner"), "corner"),
                        G.Vector3d.XAxis, G.Vector3d.YAxis)
        rect = G.Rectangle3d(plane, float(p.get("width")), float(p.get("height")))
        gid = doc.Objects.AddCurve(rect.ToNurbsCurve(), attrs)
    elif t == "interp_curve":
        crv = G.Curve.CreateInterpolatedCurve(_pts(p.get("points") or []),
                                              int(p.get("degree") or 3))
        if crv is None:
            raise ValueError("곡선을 만들 수 없습니다. 점을 확인하세요.")
        gid = doc.Objects.AddCurve(crv, attrs)
    elif t == "box":
        c = _pt(p.get("corner"), "corner")
        s = _vec(p.get("size"), "size")
        bb = G.BoundingBox(c, c + s)
        bb.MakeValid()
        gid = doc.Objects.AddBrep(G.Box(bb).ToBrep(), attrs)
    elif t == "cylinder":
        axis = _vec(p.get("axis") or [0, 0, 1])
        axis.Unitize()
        circle = G.Circle(G.Plane(_pt(p.get("base"), "base"), axis),
                          float(p.get("radius")))
        cyl = G.Cylinder(circle, float(p.get("height")))
        gid = doc.Objects.AddBrep(cyl.ToBrep(True, True), attrs)
    elif t == "sphere":
        sph = G.Sphere(_pt(p.get("center"), "center"), float(p.get("radius")))
        gid = doc.Objects.AddBrep(sph.ToBrep(), attrs)
    else:
        raise ValueError("지원하지 않는 type: %s "
                         "(point, line, polyline, circle, rectangle, "
                         "interp_curve, box, cylinder, sphere)" % t)

    if gid == Guid.Empty:
        raise ValueError("객체를 추가하지 못했습니다 (형상이 유효하지 않을 수 있음).")
    return _summary(doc, doc.Objects.FindId(gid))


def cmd_modify_objects(doc, p):
    ids = p.get("ids") or []
    if not ids:
        raise ValueError("ids 가 비어 있습니다.")

    xf = G.Transform.Identity
    has_xf = False
    sc_ = p.get("scale")
    if sc_ is not None:
        origin = _pt(p.get("scale_origin") or [0, 0, 0])
        if isinstance(sc_, (list, tuple)):
            plane = G.Plane(origin, G.Vector3d.XAxis, G.Vector3d.YAxis)
            s = G.Transform.Scale(plane, float(sc_[0]), float(sc_[1]), float(sc_[2]))
        else:
            s = G.Transform.Scale(origin, float(sc_))
        xf = s * xf
        has_xf = True
    rot = p.get("rotate")
    if rot:
        r = G.Transform.Rotation(math.radians(float(rot.get("angle", 0))),
                                 _vec(rot.get("axis") or [0, 0, 1]),
                                 _pt(rot.get("center") or [0, 0, 0]))
        xf = r * xf
        has_xf = True
    if p.get("translate") is not None:
        xf = G.Transform.Translation(_vec(p.get("translate"))) * xf
        has_xf = True

    copy = bool(p.get("copy"))
    results = []
    for s in ids:
        obj = _get_obj(doc, s)
        gid = obj.Id
        if has_xf:
            gid = doc.Objects.Transform(obj.Id, xf, not copy)
            if gid == Guid.Empty:
                raise ValueError("변형 실패: %s" % s)
        if p.get("layer") or p.get("name") is not None or p.get("color"):
            o = doc.Objects.FindId(gid)
            attrs = o.Attributes.Duplicate()
            if p.get("layer"):
                attrs.LayerIndex = _ensure_layer(doc, p.get("layer"))
            if p.get("name") is not None:
                attrs.Name = p.get("name")
            if p.get("color"):
                attrs.ObjectColor = _to_color(p.get("color"))
                attrs.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
            doc.Objects.ModifyAttributes(o, attrs, True)
        results.append(_summary(doc, doc.Objects.FindId(gid)))
    return {"objects": results}


def cmd_delete_objects(doc, p):
    deleted = 0
    missing = []
    for s in p.get("ids") or []:
        obj = doc.Objects.FindId(_guid(s))
        if obj is None:
            missing.append(s)
        elif doc.Objects.Delete(obj, True):
            deleted += 1
    return {"deleted": deleted, "not_found": missing}


def cmd_create_layer(doc, p):
    name = p.get("name")
    if not name:
        raise ValueError("레이어 이름이 필요합니다.")
    idx = _ensure_layer(doc, name, p.get("color"))
    if p.get("color"):
        layer = doc.Layers[idx]
        layer.Color = _to_color(p.get("color"))
        doc.Layers.Modify(layer, idx, True)
    if p.get("set_current"):
        doc.Layers.SetCurrentLayerIndex(idx, True)
    return {"name": doc.Layers[idx].FullPath, "index": idx}


def cmd_select_objects(doc, p):
    if p.get("clear", True):
        doc.Objects.UnselectAll()
    n = 0
    for s in p.get("ids") or []:
        obj = doc.Objects.FindId(_guid(s))
        if obj is not None and obj.Select(True) > 0:
            n += 1
    return {"selected": n}


# ---------------------------------------------------------------------------
# 명령: Python 코드 실행
# ---------------------------------------------------------------------------
def cmd_execute_python(doc, p):
    from StringIO import StringIO  # IronPython 2.7
    code = p.get("code") or ""
    ns = {"rs": rs, "sc": sc, "Rhino": Rhino, "G": G, "System": System,
          "doc": doc, "math": math, "__name__": "__rhino_mcp__"}
    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        exec(code, ns)
    finally:
        sys.stdout = old
    out = buf.getvalue()
    if len(out) > MAX_STDOUT:
        out = out[:MAX_STDOUT] + "\n... (출력 생략)"
    return {"stdout": out, "result": _jsonable(ns.get("result"))}


# ---------------------------------------------------------------------------
# 명령: 뷰 캡처
# ---------------------------------------------------------------------------
def cmd_capture_viewport(doc, p):
    name = p.get("view")
    view = doc.Views.Find(name, False) if name else doc.Views.ActiveView
    if view is None:
        raise ValueError("뷰를 찾을 수 없습니다: %s" % name)
    mode_name = p.get("display_mode")
    if mode_name:
        mode = None
        want = mode_name.strip().lower()
        for m in Rhino.Display.DisplayModeDescription.GetDisplayModes():
            if want in ((m.EnglishName or "").lower(), (m.LocalName or "").lower()):
                mode = m
                break
        if mode is None:
            raise ValueError("표시 모드를 찾을 수 없습니다: %s" % mode_name)
        view.ActiveViewport.DisplayMode = mode
    if p.get("zoom_extents"):
        view.ActiveViewport.ZoomExtents()
    view.Redraw()
    w = max(100, min(int(p.get("width") or 800), 2000))
    h = max(100, min(int(p.get("height") or 600), 2000))
    bmp = view.CaptureToBitmap(System.Drawing.Size(w, h))
    if bmp is None:
        raise ValueError("뷰 캡처에 실패했습니다.")
    ms = MemoryStream()
    try:
        bmp.Save(ms, System.Drawing.Imaging.ImageFormat.Png)
        data = System.Convert.ToBase64String(ms.ToArray())
    finally:
        bmp.Dispose()
        ms.Dispose()
    return {"view": view.ActiveViewport.Name, "width": w, "height": h,
            "png_base64": data}


def cmd_ping(doc, p):
    return {"rhino": str(Rhino.RhinoApp.Version), "document": doc.Name}


COMMANDS = {
    "ping": cmd_ping,
    "get_document_info": cmd_get_document_info,
    "list_objects": cmd_list_objects,
    "get_object_info": cmd_get_object_info,
    "create_object": cmd_create_object,
    "modify_objects": cmd_modify_objects,
    "delete_objects": cmd_delete_objects,
    "create_layer": cmd_create_layer,
    "select_objects": cmd_select_objects,
    "execute_python": cmd_execute_python,
    "capture_viewport": cmd_capture_viewport,
}
MUTATING = set(["create_object", "modify_objects", "delete_objects",
                "create_layer", "select_objects", "execute_python"])


# ---------------------------------------------------------------------------
# 요청 처리 (UI 스레드)
# ---------------------------------------------------------------------------
def _handle(req):
    cmd = req.get("command")
    p = req.get("params") or {}
    fn = COMMANDS.get(cmd)
    if fn is None:
        return {"ok": False, "error": "알 수 없는 명령: %s" % cmd}
    doc = _doc()
    undo = doc.BeginUndoRecord("MCP " + cmd) if cmd in MUTATING else None
    try:
        return {"ok": True, "result": fn(doc, p)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                "trace": traceback.format_exc()[-2000:]}
    finally:
        if undo is not None:
            doc.EndUndoRecord(undo)
        if cmd in MUTATING:
            doc.Views.Redraw()


def _run_on_ui(req):
    box = {}
    done = ManualResetEvent(False)

    def work():
        try:
            box["resp"] = _handle(req)
        except Exception as e:
            box["resp"] = {"ok": False, "error": str(e)}
        finally:
            done.Set()

    Rhino.RhinoApp.InvokeOnUiThread(Action(work))
    if not done.WaitOne(UI_TIMEOUT_MS):
        return {"ok": False,
                "error": "Rhino 응답 시간 초과. 실행 중인 명령이 있으면 Esc 로 끝내 주세요."}
    return box["resp"]


# ---------------------------------------------------------------------------
# 소켓 리스너 (백그라운드 스레드)
# ---------------------------------------------------------------------------
class Listener(object):
    def __init__(self, port):
        self.port = port
        self.running = False
        self.server = None
        self.thread = None

    def start(self):
        self.server = TcpListener(IPAddress.Parse(HOST), self.port)
        self.server.Server.SetSocketOption(SocketOptionLevel.Socket,
                                           SocketOptionName.ReuseAddress, True)
        self.server.Start()
        self.running = True
        self.thread = Thread(ThreadStart(self._loop))
        self.thread.IsBackground = True
        self.thread.Start()

    def stop(self):
        self.running = False
        try:
            self.server.Stop()
        except Exception:
            pass

    def _loop(self):
        while self.running:
            try:
                client = self.server.AcceptTcpClient()
            except Exception:
                break
            try:
                self._serve(client)
            except Exception as e:
                if self.running:
                    _log("연결 처리 중 오류: %s" % e)
            finally:
                client.Close()

    def _serve(self, client):
        enc = UTF8Encoding(False)
        stream = client.GetStream()
        reader = StreamReader(stream, enc)
        writer = StreamWriter(stream, enc)
        writer.AutoFlush = True
        while self.running:
            line = reader.ReadLine()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except Exception:
                resp = {"ok": False, "error": "잘못된 JSON 요청"}
            else:
                resp = _run_on_ui(req)
                resp["id"] = req.get("id")
            writer.WriteLine(json.dumps(_jsonable(resp)))


def main():
    old = sc.sticky.get(STICKY_KEY)
    if old is not None:
        old.stop()
        _log("기존 리스너를 중지했습니다.")
    listener = Listener(PORT)
    try:
        listener.start()
    except Exception as e:
        _log("포트 %d 를 열 수 없습니다: %s" % (PORT, e))
        return
    sc.sticky[STICKY_KEY] = listener
    _log("%s:%d 에서 Claude 연결을 기다리는 중입니다." % (HOST, PORT))


if __name__ == "__main__":
    main()
