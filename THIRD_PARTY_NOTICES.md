# Third-party notices

## Open3D

The files under `src/open3d_reconstruct/vendor/reconstruction_system/` are derived from the Open3D 0.19.0 Python reconstruction-system examples and retain their original copyright and SPDX headers.

Open3D is Copyright (c) 2018-2024 www.open3d.org and is distributed under the MIT License: <https://github.com/isl-org/Open3D/blob/v0.19.0/LICENSE>.

## Azure Kinect Sensor SDK

`setup.sh` downloads the unmodified Microsoft `libk4a1.4_1.4.1_amd64.deb`
package on Linux. `setup.ps1` downloads the unmodified
`Microsoft.Azure.Kinect.Sensor` 1.4.1 NuGet package on Windows. Both are
extracted locally and include `libk4a`, `libk4arecord`, the proprietary Azure
Kinect depth engine, license terms, redistribution list, and third-party
notices.

After setup, those documents are available at:

- `.deps/k4a/usr/share/doc/libk4a1.4/LICENSE.txt`
- `.deps/k4a/usr/share/doc/libk4a1.4/REDIST.txt`
- `.deps/k4a/usr/share/doc/libk4a1.4/ThirdPartyNotices.txt`

On Windows they are available at:

- `.deps/k4a-windows/LICENSE.txt`
- `.deps/k4a-windows/REDIST.txt`
- `.deps/k4a-windows/ThirdPartyNotices.txt`

Microsoft's upstream project is archived at <https://github.com/microsoft/Azure-Kinect-Sensor-SDK>.

## Intel RealSense librealsense

The Open3D 0.19.0 wheel is built with `BUILD_LIBREALSENSE=ON` and statically
integrates librealsense v2.44.0. No separate system librealsense package or
`pyrealsense2` package is installed by this project.

`config/99-realsense-libusb.rules` contains the D435/D435i-specific subset of
librealsense v2.44.0's official Linux udev rules. librealsense is distributed
under the Apache License 2.0:
<https://github.com/IntelRealSense/librealsense/blob/v2.44.0/LICENSE>.

## Python packages and managed Python

Python package names, versions, sources, and hashes are recorded in `uv.lock`.
CPython is installed locally by `uv` from Astral's python-build-standalone
distributions. Each installed distribution retains its own license metadata in
`.venv`/`.python` or the Windows-specific `.venv-windows`/`.python-windows`
directories.
