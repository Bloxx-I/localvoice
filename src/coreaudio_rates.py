"""Read the physical Core Audio rate ranges; never alter device settings."""
import ctypes as C
import sys


def fourcc(value):
    return int.from_bytes(value.encode('ascii'), 'big')


def hardware_rates():
    if sys.platform != 'darwin':
        return {}
    try:
        ca = C.CDLL('/System/Library/Frameworks/CoreAudio.framework/CoreAudio')
        cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        class Address(C.Structure):
            _fields_ = [('selector', C.c_uint32), ('scope', C.c_uint32), ('element', C.c_uint32)]
        class Range(C.Structure):
            _fields_ = [('minimum', C.c_double), ('maximum', C.c_double)]
        ca.AudioObjectGetPropertyDataSize.argtypes = [C.c_uint32, C.POINTER(Address), C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32)]
        ca.AudioObjectGetPropertyData.argtypes = [C.c_uint32, C.POINTER(Address), C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32), C.c_void_p]
        cf.CFStringGetCString.argtypes = [C.c_void_p, C.c_char_p, C.c_long, C.c_uint32]
        cf.CFRelease.argtypes = [C.c_void_p]
        def read(device, selector, ctype):
            address = Address(fourcc(selector), fourcc('glob'), 0)
            size = C.c_uint32()
            if ca.AudioObjectGetPropertyDataSize(device, C.byref(address), 0, None, C.byref(size)):
                return []
            array = (ctype * (size.value // C.sizeof(ctype)))()
            if ca.AudioObjectGetPropertyData(device, C.byref(address), 0, None, C.byref(size), array):
                return []
            return list(array)
        result = {}
        for device in read(1, 'dev#', C.c_uint32):
            names = read(device, 'lnam', C.c_void_p)
            if not names:
                continue
            buffer = C.create_string_buffer(2048)
            cf.CFStringGetCString(names[0], buffer, len(buffer), 0x08000100)
            cf.CFRelease(names[0])
            ranges = read(device, 'nsr#', Range)
            current = read(device, 'nsrt', C.c_double)
            result[buffer.value.decode('utf-8')] = {
                'ranges': [[r.minimum, r.maximum] for r in ranges],
                'current': current[0] if current else None}
        return result
    except Exception:
        return {}


def supports_rate(name, rate):
    info = hardware_rates().get(name)
    if not info or not info['ranges']:
        return None
    return any(low <= rate <= high for low, high in info['ranges'])
