"""
Claude 없이 Rhino 리스너 연결만 확인하는 스크립트.
사용: python test_connection.py
"""
import sys

from rhino_mcp_server import RhinoError, send

try:
    info = send("ping")
    print("연결 성공:", info)
    doc = send("get_document_info")
    print("단위:", doc["units"], "/ 객체 수:", doc["object_count"],
          "/ 레이어 수:", len(doc["layers"]))
except RhinoError as e:
    print("연결 실패:", e)
    sys.exit(1)
