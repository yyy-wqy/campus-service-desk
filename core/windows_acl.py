"""Apply and read back a protected DACL for Windows operational storage."""

import ctypes as c
from ctypes import wintypes as w
import stat


TOKEN_QUERY = 0x0008
TOKEN_USER = 1
ERROR_INSUFFICIENT_BUFFER = 122
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SDDL_REVISION = 1
SYSTEM_SID = "S-1-5-18"


def bind(dll, name, restype, *argtypes):
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


class SecurityAPI:
    def __init__(self):
        kernel = c.WinDLL("kernel32", use_last_error=True)
        security = c.WinDLL("advapi32", use_last_error=True)
        pointer = c.c_void_p
        pointer_out = c.POINTER(pointer)
        dword_out = c.POINTER(w.DWORD)
        bool_out = c.POINTER(w.BOOL)
        self.current_process = bind(kernel, "GetCurrentProcess", w.HANDLE)
        self.close_handle = bind(kernel, "CloseHandle", w.BOOL, w.HANDLE)
        self.free = bind(kernel, "LocalFree", pointer, pointer)
        self.open_token = bind(security, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, pointer_out)
        self.token_info = bind(security, "GetTokenInformation", w.BOOL, w.HANDLE, c.c_int, pointer, w.DWORD, dword_out)
        self.sid_to_string = bind(security, "ConvertSidToStringSidW", w.BOOL, pointer, pointer_out)
        self.from_sddl = bind(security, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                              w.BOOL, w.LPCWSTR, w.DWORD, pointer_out, dword_out)
        self.to_sddl = bind(security, "ConvertSecurityDescriptorToStringSecurityDescriptorW",
                            w.BOOL, pointer, w.DWORD, w.DWORD, pointer_out, dword_out)
        self.get_dacl = bind(security, "GetSecurityDescriptorDacl", w.BOOL,
                             pointer, bool_out, pointer_out, bool_out)
        self.get_file_security = bind(security, "GetNamedSecurityInfoW", w.DWORD, w.LPCWSTR,
                                      c.c_int, w.DWORD, pointer_out, pointer_out,
                                      pointer_out, pointer_out, pointer_out)
        self.set_file_security = bind(security, "SetNamedSecurityInfoW", w.DWORD, w.LPCWSTR,
                                      c.c_int, w.DWORD, pointer, pointer, pointer, pointer)

    def user_sid(self):
        token = w.HANDLE()
        if not self.open_token(self.current_process(), TOKEN_QUERY, c.byref(token)):
            raise c.WinError(c.get_last_error())
        try:
            size = w.DWORD()
            self.token_info(token, TOKEN_USER, None, 0, c.byref(size))
            if c.get_last_error() != ERROR_INSUFFICIENT_BUFFER:
                raise c.WinError(c.get_last_error())
            buffer = c.create_string_buffer(size.value)
            if not self.token_info(token, TOKEN_USER, buffer, size, c.byref(size)):
                raise c.WinError(c.get_last_error())
            # TOKEN_USER starts with SID_AND_ATTRIBUTES whose first member is PSID.
            sid = c.c_void_p.from_buffer(buffer)
            address = c.c_void_p()
            if not self.sid_to_string(sid, c.byref(address)):
                raise c.WinError(c.get_last_error())
            try:
                return c.wstring_at(address)
            finally:
                self.free(address)
        finally:
            self.close_handle(token)

    def descriptor_text(self, descriptor):
        address = c.c_void_p()
        if not self.to_sddl(descriptor, SDDL_REVISION, DACL_SECURITY_INFORMATION, c.byref(address), None):
            raise c.WinError(c.get_last_error())
        try:
            return c.wstring_at(address)
        finally:
            self.free(address)

    def file_dacl(self, path):
        descriptor = c.c_void_p()
        error = self.get_file_security(str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                                       None, None, None, None, c.byref(descriptor))
        if error:
            raise c.WinError(error)
        try:
            return self.descriptor_text(descriptor)
        finally:
            self.free(descriptor)

    def replace_dacl(self, path, sddl):
        descriptor = c.c_void_p()
        if not self.from_sddl(sddl, SDDL_REVISION, c.byref(descriptor), None):
            raise c.WinError(c.get_last_error())
        try:
            expected = self.descriptor_text(descriptor)
            if matches_acl(self.file_dacl(path), expected):
                return
            present, defaulted, acl = w.BOOL(), w.BOOL(), c.c_void_p()
            if not self.get_dacl(descriptor, c.byref(present), c.byref(acl), c.byref(defaulted)):
                raise c.WinError(c.get_last_error())
            if not present or not acl:
                raise PermissionError("私有目录的 ACL 无效。")
            error = self.set_file_security(
                str(path), SE_FILE_OBJECT,
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                None, None, acl, None,
            )
            if error:
                raise c.WinError(error)
            if not matches_acl(self.file_dacl(path), expected):
                raise PermissionError("无法确认运行目录的私有访问权限。")
        finally:
            self.free(descriptor)


def matches_acl(actual, expected):
    # NTFS may add auto-inheritance metadata. Protection and the ACEs must match.
    flags, separator, rules = actual.partition("(")
    return flags.startswith("D:P") and separator + rules == "(" + expected.split("(", 1)[1]


def secure_tree(root):
    api = SecurityAPI()
    allowed = tuple(dict.fromkeys((api.user_sid(), SYSTEM_SID)))

    def secure(path):
        info = path.lstat()
        if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise PermissionError("运行目录不能包含重解析点。")
        directory = stat.S_ISDIR(info.st_mode)
        flags = "OICI" if directory else ""
        rules = "".join(f"(A;{flags};FA;;;{sid})" for sid in allowed)
        api.replace_dacl(path, "D:P" + rules)
        if directory:
            for child in path.iterdir():
                secure(child)

    secure(root)
