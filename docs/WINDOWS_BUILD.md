# Building qdmr on Windows 11 (MSYS2 / MinGW-w64)

Not tested on a Windows machine. The package names below are from memory of the MSYS2
repositories. If `pacman` says one is missing, search for it with `pacman -Ss <name>`.

## 1. Install

1. **MSYS2**: download the installer from https://www.msys2.org and install to `C:\msys64`.
2. **Prolific USB driver**: the DM-32UV uses a Prolific chip (USB ID 067B:23A3). Windows
   usually installs a driver itself. After plugging the radio in, check Device Manager under
   "Ports (COM & LPT)" for a COM port number. A yellow warning means the driver does not
   accept your chip version.
3. **Git**: installed with the packages below, or use Git for Windows.

## 2. Install the build packages

Open **MSYS2 MINGW64** (not "MSYS2 MSYS", not UCRT64) from the Start menu and run:

```sh
pacman -Syu
pacman -S --needed git \
  mingw-w64-x86_64-toolchain \
  mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja \
  mingw-w64-x86_64-qt6-base mingw-w64-x86_64-qt6-tools \
  mingw-w64-x86_64-qt6-serialport mingw-w64-x86_64-qt6-positioning \
  mingw-w64-x86_64-qt6-multimedia \
  mingw-w64-x86_64-yaml-cpp mingw-w64-x86_64-libusb mingw-w64-x86_64-librsvg
```

(`pacman -Syu` may close the window once. Reopen MINGW64 and run it again.)

## 3. Get the code and apply the patch

Copy `windows-build.patch` into your MSYS2 home folder, then:

```sh
git clone https://github.com/hmatuschek/qdmr.git
cd qdmr
git apply ../windows-build.patch
```

The patch does two things:
- builds the core library (`libdmrconf`) as a static library, since it has no DLL export macros;
- builds `dmrconf` as a console program so it can print to the terminal.

## 4. Configure and build

```sh
mkdir build && cd build
cmake -G Ninja .. -DCMAKE_BUILD_TYPE=Release -DINSTALL_APPSTREAM_DATA=OFF
ninja
```

## 5. Run

In the same MINGW64 shell:

```sh
./cli/dmrconf.exe --help
./src/qdmr.exe
```

Run from the MINGW64 shell so the Qt and library DLLs are found. For a copy that runs
anywhere, use `windeployqt6 src/qdmr.exe` and copy the DLLs it lists.

## If something fails

- **CMake cannot find Qt6:** add `-DQt6_ROOT=/mingw64`.
- **libusb or yaml-cpp not found:** add `-DCMAKE_PREFIX_PATH=/mingw64`.
- **`rsvg-convert` not found:** reinstall `mingw-w64-x86_64-librsvg`.
- **Compile error about a POSIX call (`usleep`, `unistd.h`):** send me the message. These
  only appear in code for other radios, and are easy to replace.

## Before writing to a radio

Read a codeplug first and save a backup copy. Test writes on a spare radio.
The DM-32UV protocol is documented at https://github.com/infamy/DM32-Protocol-Spec.
