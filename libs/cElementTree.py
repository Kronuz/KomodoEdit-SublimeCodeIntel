import sys
import struct

VERSION = sys.version_info[:2]
PLATFORM = sys.platform
ARCH = 'x%d' % (struct.calcsize('P') * 8)

if VERSION >= (3, 3):
    from iElementTree import *  # NOQA
    from iElementTree import _patched_for_komodo_  # NOQA
elif VERSION >= (2, 6):
    platform = None

    try:
        from _local_arch.cElementTree import *  # NOQA
        platform = "Local arch"
    except ImportError:
        if PLATFORM == 'darwin':
            from _macosx_universal_py26.cElementTree import *  # NOQA
            platform = "MacOS X Universal"
        elif PLATFORM.startswith('linux'):
            if ARCH == 'x64':
                from _linux_libcpp6_x86_64_py26.cElementTree import *  # NOQA
                platform = "Linux 64 bits"
            elif ARCH == 'x32':
                from _linux_libcpp6_x86_py26.cElementTree import *  # NOQA
                platform = "Linux 32 bits"
        elif PLATFORM.startswith('win'):
            if ARCH == 'x64':
                from _win64_py26.cElementTree import *  # NOQA
                platform = "Windows 64 bits"
            elif ARCH == 'x32':
                from _win32_py26.cElementTree import *  # NOQA
                platform = "Windows 32 bits"

    if not platform:
        raise ImportError("Could not find a suitable cElementTree binary for your platform and architecture.")
