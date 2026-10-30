"""Stub minimal modul langflow agar file komponen di folder components/ bisa
dipakai ulang oleh backend tanpa menginstal Langflow."""
import sys
import types


class _Input:
    def __init__(self, name, value=None, **kw):
        self.name, self.value = name, value


class Component:
    def __init__(self, **kwargs):
        for inp in self.inputs:
            setattr(self, inp.name, inp.value)
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.status = None


class Message:
    def __init__(self, text=""):
        self.text = text


def install():
    if "langflow.custom" in sys.modules:
        return
    lf = types.ModuleType("langflow")
    m_custom = types.ModuleType("langflow.custom"); m_custom.Component = Component
    m_io = types.ModuleType("langflow.io")
    for n in ["BoolInput", "IntInput", "MessageTextInput", "MultilineInput", "StrInput"]:
        setattr(m_io, n, _Input)
    m_io.Output = lambda **kw: kw
    m_schema = types.ModuleType("langflow.schema")
    m_msg = types.ModuleType("langflow.schema.message"); m_msg.Message = Message
    sys.modules.update({"langflow": lf, "langflow.custom": m_custom, "langflow.io": m_io,
                        "langflow.schema": m_schema, "langflow.schema.message": m_msg})
