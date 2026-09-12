#!/usr/bin/env python3
"""Prepare pinned hardware sources, build host/firmware artifacts, and package frontends."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEVICES = {"hackrf": "hackrf_pro", "pocketsdr": "pocketsdr"}


def run(args, *, cwd=ROOT, **kwargs):
    print("+", shlex.join(str(arg) for arg in args), flush=True)
    return subprocess.run([str(arg) for arg in args], cwd=cwd, check=True, **kwargs)


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args])


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def specification(device):
    path = ROOT / "hardware" / DEVICES[device] / "build.json"
    spec = json.loads(path.read_text())
    if spec["schema_version"] != 1:
        raise RuntimeError(f"Unsupported manifest: {path}")
    return spec


def source_info(spec):
    source = ROOT / spec["source"]
    revision = git(source, "rev-parse", "HEAD").decode().strip()
    entries = git(ROOT, "ls-files", "--stage", "--", spec["source"]).decode().splitlines()
    if len(entries) != 1 or entries[0].split()[0] != "160000":
        raise RuntimeError(f"{spec['source']} must be a registered submodule")
    pin = entries[0].split()[1]
    if revision != pin:
        raise RuntimeError(f"{spec['source']} is at {revision}, but the parent pins {pin}; "
                           "review and stage the submodule revision before building")
    if git(source, "status", "--porcelain", "--untracked-files=normal").strip():
        raise RuntimeError(f"{spec['source']} has uncommitted changes; commit the hardware "
                           "candidate and stage its submodule pin before building")
    return {"path": spec["source"], "revision": revision,
            "url": git(source, "remote", "get-url", "origin").decode().strip(),
            "upstream": spec["upstream"], "upstream_base": spec["upstream_base"],
            "submodules": git(source, "submodule", "status", "--recursive").decode().splitlines()}


def hashes(directory):
    return {p.relative_to(directory).as_posix(): digest(p.read_bytes())
            for p in sorted(directory.rglob("*")) if p.is_file()}


def verify_artifacts(directory, expected):
    for name, checksum in expected.items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()):
            raise RuntimeError(f"Artifact escapes its package: {name}")
        if not path.is_file() or digest(path.read_bytes()) != checksum:
            raise RuntimeError(f"Artifact missing or modified: {path}")


def snapshot(source, destination, paths):
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        archive = git(source, "archive", "HEAD", "--", *paths)
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(temporary, filter="data")
        Path(temporary).rename(destination)


def prepare(device, *, firmware=False, update=False):
    spec = specification(device)
    if update or not (ROOT / spec["source"] / ".git").exists():
        run(["git", "submodule", "update", "--init", "--", spec["source"]])
    if firmware:
        for submodule in spec["firmware"].get("submodules", []):
            run(["git", "submodule", "update", "--init", "--recursive", "--", submodule],
                cwd=ROOT / spec["source"])
    return spec


def compiler_version(command):
    return subprocess.check_output([command, "--version"], text=True).splitlines()[0]


def activate(directory, name, target):
    link = directory / name
    if link.exists() and not link.is_symlink():
        raise RuntimeError(f"Refusing to replace an existing directory: {link}")
    temporary = directory / (name + ".next")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target.relative_to(directory), target_is_directory=True)
    temporary.replace(link)


def build_host(device, output_root, jobs):
    spec = prepare(device)
    host = spec["host"]
    if platform.system() != host["platform"] or platform.machine() not in host["architectures"]:
        raise RuntimeError(f"{device} host build currently targets {host['platform']} {host['architectures']}")
    source = ROOT / spec["source"]
    recipe = Path(__file__).read_bytes()
    info = {"schema_version": 1, "device": device, "source": source_info(spec),
            "recipe_sha256": digest(recipe), "specification": spec,
            "toolchain": {name: compiler_version(host[name]) for name in ("cc", "cxx")},
            "architecture": platform.machine()}
    key = digest(json.dumps(info, sort_keys=True).encode())[:20]
    directory = output_root / device
    work = directory / "work" / key
    prefix = work / "install"
    record = prefix / "build-info.json"
    if record.exists():
        verify_artifacts(prefix, json.loads(record.read_text())["artifacts"])
        activate(directory, "install", prefix)
        print(f"{device}: verified existing host build {key}")
        return
    work.mkdir(parents=True, exist_ok=True)
    if device == "hackrf":
        run(["cmake", "-S", source / "host", "-B", work / "host", "-G", "Ninja",
             "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={prefix}",
             "-DCMAKE_INSTALL_LIBDIR=lib", f"-DCMAKE_C_COMPILER={host['cc']}",
             *host["cmake_options"]])
        run(["cmake", "--build", work / "host", "--parallel", jobs])
        run(["cmake", "--install", work / "host"])
        (prefix / "licenses").mkdir(exist_ok=True)
        shutil.copy2(source / "COPYING", prefix / "licenses/COPYING.HackRF")
        notices = []
        for name in ("hackrf.c", "hackrf.h"):
            text = (source / "host/libhackrf/src" / name).read_text()
            notices.append(text[:text.index("*/") + 2])
        (prefix / "licenses/LICENSE.libhackrf.txt").write_text("\n\n".join(notices) + "\n")
    else:
        tree = work / "source"
        snapshot(source, tree, ["src", "lib/build", "lib/RTKLIB", "app/pocket_scan",
                                "app/pocket_conf", "app/pocket_dump", "app/pocket_acq", "LICENSE.txt"])
        options = host["make_options"] + [f"CC={host['cc']}", f"CX={host['cxx']}", f"LD={host['cxx']}"]
        for makefile in ("librtk.mk", "libsdr.mk"):
            run(["make", "-C", tree / "lib/build", "-f", makefile, f"-j{jobs}", *options])
        for folder in ("lib", "bin", "include/pocketsdr", "licenses"):
            (prefix / folder).mkdir(parents=True, exist_ok=True)
        (tree / "lib/linux").mkdir(exist_ok=True)
        for name in ("libsdr.a", "librtk.a"):
            shutil.copy2(tree / "lib/build" / name, prefix / "lib" / name)
            shutil.copy2(prefix / "lib" / name, tree / "lib/linux" / name)
        for tool in ("pocket_scan", "pocket_conf", "pocket_dump", "pocket_acq"):
            run(["make", "-C", tree / "app" / tool, f"-j{jobs}", "USE_SOAPY=0", "USE_FFTW=1"])
            shutil.copy2(tree / "app" / tool / tool, prefix / "bin" / tool)
        shutil.copy2(tree / "src/pocket_sdr.h", prefix / "include/pocketsdr")
        shutil.copy2(tree / "lib/RTKLIB/src/rtklib.h", prefix / "include/pocketsdr")
        shutil.copy2(tree / "LICENSE.txt", prefix / "licenses/LICENSE.PocketSDR.txt")
        shutil.copy2(tree / "lib/RTKLIB/readme.txt", prefix / "licenses/README.RTKLIB.txt")
    (prefix / "build-recipe.py").write_bytes(recipe)
    (prefix / "udev").mkdir(exist_ok=True)
    rule = (source / "host/libhackrf/53-hackrf.rules" if device == "hackrf"
            else source / "driver/99-pocket-sdr.rules")
    shutil.copy2(rule, prefix / "udev" / rule.name)
    info["artifacts"] = hashes(prefix)
    write_json(record, info)
    activate(directory, "install", prefix)
    print(f"{device}: staged at {directory / 'install'}")


def build_pocketsdr_linux(project, image_root, sdk_root, firmware):
    build = image_root / "Release"
    build.mkdir(parents=True, exist_ok=True)
    compiler = firmware["compiler"]
    library = sdk_root / firmware["sdk_library_directory"]
    target = ["-mcpu=arm926ej-s", "-marm", "-mthumb-interwork"]
    # Match the pinned Eclipse Release configuration, including its library order.
    common = [*target, "-Os", "-fsigned-char", "-ffunction-sections", "-fdata-sections",
              "-Wall", "-fmessage-length=0", "-I", library / "inc"]
    objects = []
    for name in ("cyfx_gcc_startup.S", "cyfxtx.c", "pocket_fw_v3.c", "pocket_usb_dscr.c"):
        output = build / (Path(name).stem + ".o")
        flags = (["-x", "assembler-with-cpp"] if name.endswith(".S")
                 else ["-std=c99", "-D__CYU3P_TX__=1", "-DREV_A"])
        run([compiler, *common, *flags, "-c", project / name, "-o", output])
        objects.append(output)
    elf = build / "pocket_fw_v3.elf"
    run([compiler, *target, "-nostartfiles", *objects,
         "-T", sdk_root / "fw_build/fx3_fw/fx3.ld", "-L", library / "fx3_release",
         "-Wl,-d,--gc-sections,--no-wchar-size-warning,--entry,CyU3PFirmwareEntry",
         f"-Wl,-Map,{build / 'pocket_fw_v3.map'}",
         "-lcyu3lpp", "-lcyfxapi", "-lcyu3threadx", "-lc", "-lgcc", "-o", elf])
    converter = build / "elf2img"
    run([firmware["host_compiler"], "-O2", "-std=c99",
         sdk_root / "util/elf2img/elf2img.c", "-o", converter])
    run([converter, "-i", elf, "-o", build / "pocket_fw_v3.img", "-v"])


def build_firmware(device, output_root, jobs, sdk_root):
    spec = specification(device)
    firmware = spec["firmware"]
    host = platform.system()
    platforms = firmware.get("platforms", [firmware.get("platform")])
    if host not in platforms:
        raise RuntimeError(f"{device} firmware requires {' or '.join(platforms)} and "
                           f"{firmware.get('sdk', firmware.get('compiler'))}; run this explicit "
                           "firmware target on that build host. Host frontend builds do not require it.")
    if device == "pocketsdr":
        if sdk_root is None or not (sdk_root / firmware["sdk_library_directory"]).is_dir():
            raise RuntimeError("--sdk-root must point to the installed Cypress FX3 SDK with fw_lib/1_3_5")
        if host == "Linux":
            toolchain = {"sdk": firmware["sdk"], "platform": host,
                         "compiler": compiler_version(firmware["compiler"]),
                         "host_compiler": compiler_version(firmware["host_compiler"])}
        else:
            builder = sdk_root / "Eclipse/ezUsbSuite.exe"
            if not builder.is_file():
                raise RuntimeError(f"Missing FX3 SDK builder: {builder}")
            toolchain = firmware["sdk"]
    else:
        toolchain = compiler_version(firmware["compiler"])
    prepare(device, firmware=True)
    source = ROOT / spec["source"]
    recipe = Path(__file__).read_bytes()
    info = {"schema_version": 1, "device": device, "source": source_info(spec),
            "specification": spec, "toolchain": toolchain,
            "recipe_sha256": digest(recipe)}
    if device == "pocketsdr" and host == "Linux":
        library = sdk_root / firmware["sdk_library_directory"]
        sdk_files = [*(library / "inc").glob("*.h"),
                     *(library / "fx3_release").glob("*.a"),
                     sdk_root / "fw_build/fx3_fw/fx3.ld",
                     sdk_root / "util/elf2img/elf2img.c"]
        info["sdk_files"] = {p.relative_to(sdk_root).as_posix(): digest(p.read_bytes())
                             for p in sorted(sdk_files)}
    key = digest(json.dumps(info, sort_keys=True).encode())[:20]
    directory = output_root / device
    work = directory / "firmware-work" / key
    tree = work / "source"
    snapshot(source, tree, ["firmware", "COPYING"] if device == "hackrf"
             else [firmware["project"], "LICENSE.txt"])
    if device == "hackrf":
        # The parent archive contains gitlinks, not the nested firmware source.
        nested = "firmware/libopencm3"
        destination = tree / nested
        if destination.is_dir() and not any(destination.iterdir()):
            destination.rmdir()
        snapshot(source / nested, destination, [])
        build = work / "build"
        objcopy = shutil.which(firmware["objcopy"])
        if objcopy is None:
            raise RuntimeError(f"Missing firmware tool: {firmware['objcopy']}")
        # Upstream's toolchain forcibly resets its cache entry during project().
        overrides = work / "tools.cmake"
        overrides.write_text(f'set(CMAKE_OBJCOPY [==[{objcopy}]==])\n')
        run(["cmake", "-S", tree / firmware["project"], "-B", build,
             f"-DBOARD={firmware['board']}",
             f"-DVERSION=git-{info['source']['revision'][:8]}",
             f"-DCMAKE_PROJECT_INCLUDE={overrides}"])
        run(["cmake", "--build", build, "--parallel", jobs])
        image_root = build
    elif host == "Linux":
        image_root = work / "build"
        build_pocketsdr_linux(tree / firmware["project"], image_root, sdk_root, firmware)
    else:
        environment = os.environ.copy()
        environment["FX3_INSTALL_PATH"] = str(sdk_root)
        run([builder, "-nosplash", "--launcher.suppressErrors", "-application",
             "org.eclipse.cdt.managedbuilder.core.headlessbuild", "-data", work / "workspace",
             "-import", tree / firmware["project"], "-cleanBuild", firmware["build_configuration"]],
            env=environment)
        image_root = tree / firmware["project"]
    artifacts = work / "artifacts"
    artifacts.mkdir(exist_ok=True)
    for name in firmware["images"]:
        shutil.copy2(image_root / name, artifacts / Path(name).name)
    shutil.copy2(source / ("COPYING" if device == "hackrf" else "LICENSE.txt"), artifacts / "LICENSE.txt")
    if device == "hackrf":
        for license_file in ("COPYING.GPL3", "COPYING.LGPL3"):
            shutil.copy2(source / "firmware/libopencm3" / license_file, artifacts / license_file)
    (artifacts / "build-recipe.py").write_bytes(recipe)
    info["artifacts"] = {name: checksum for name, checksum in hashes(artifacts).items()
                         if name != "build-info.json"}
    write_json(artifacts / "build-info.json", info)
    activate(directory, "firmware", artifacts)
    print(f"{device}: firmware built; no device was programmed")


def cmake_cache(build_directory):
    values = {}
    for line in (build_directory / "CMakeCache.txt").read_text().splitlines():
        if line.startswith(("#", "//")) or ":" not in line or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.split(":", 1)[0]] = value
    return values


def package_frontends(output, build_directory, output_root):
    if output.exists():
        raise RuntimeError(f"Package destination already exists: {output}")
    cache = cmake_cache(build_directory)
    if Path(cache.get("RX_GNSS_HARDWARE_ROOT", "")).resolve() != output_root:
        raise RuntimeError("CMake and --root select different staged hardware libraries; reconfigure first")
    if cache.get("RX_GNSS_USE_STOCK_HACKRF_FRONTEND") != "OFF":
        raise RuntimeError("The shared frontend package requires the patched HackRF build")
    if cache.get("CMAKE_INSTALL_BINDIR") != "bin" or cache.get("CMAKE_INSTALL_DATAROOTDIR") != "share":
        raise RuntimeError("The portable frontend package requires bin/ and share/ install directories")
    records = {}
    for device in DEVICES:
        spec = specification(device)
        prefix = output_root / device / "install"
        info = json.loads((prefix / "build-info.json").read_text())
        if info["source"]["revision"] != source_info(spec)["revision"] or info["specification"] != spec:
            raise RuntimeError(f"{device}: staged build is stale; rebuild the host libraries")
        verify_artifacts(prefix, info["artifacts"])
        records[device] = info
    run(["cmake", "--build", build_directory, "--target",
         "rx_gnss_hackrf_frontend", "rx_gnss_pocketsdr_frontend"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="frontends-", dir=output.parent) as temporary:
        package = Path(temporary)
        for component in ("hardware_frontends", "hackrf_frontend_desktop"):
            run(["cmake", "--install", build_directory, "--prefix", package, "--component", component])
        for device, board in DEVICES.items():
            installed = json.loads((package / "share/rx_gnss/hardware" / board / "build-info.json").read_text())
            if installed != records[device]:
                raise RuntimeError(f"{device}: installed build provenance differs from the verified stage")
        for device in DEVICES:
            firmware = output_root / device / "firmware"
            if firmware.exists():
                info = json.loads((firmware / "build-info.json").read_text())
                if info["source"]["revision"] != records[device]["source"]["revision"]:
                    raise RuntimeError(f"{device}: firmware and host source revisions differ; rebuild firmware")
                verify_artifacts(firmware, info["artifacts"])
                shutil.copytree(firmware, package / "firmware" / device)
        tracked_diff = git(ROOT, "diff", "HEAD", "--binary")
        untracked = git(ROOT, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
        metadata = {"schema_version": 1, "receiver_commit": git(ROOT, "rev-parse", "HEAD").decode().strip(),
                    "receiver_build": {key: value for key, value in cache.items()
                                       if key.startswith(("CMAKE_C_FLAGS", "CMAKE_CXX_FLAGS"))
                                       or key in ("CMAKE_BUILD_TYPE", "CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER",
                                                  "CMAKE_INTERPROCEDURAL_OPTIMIZATION", "ENABLE_ASAN", "ENABLE_UBSAN")},
                    "receiver_dirty": bool(tracked_diff or any(untracked)),
                    "receiver_diff_sha256": digest(tracked_diff),
                    "receiver_untracked_sha256": {name: digest((ROOT / name).read_bytes())
                                                  for name in untracked if name and (ROOT / name).is_file()},
                    "hardware": records, "artifacts": hashes(package)}
        write_json(package / "release.json", metadata)
        smoke_package(package)
        package.rename(output)
    print(f"Frontend package: {output}")


def smoke_package(package):
    info = json.loads((package / "release.json").read_text())
    verify_artifacts(package, info["artifacts"])
    environment = os.environ.copy()
    environment.pop("LD_LIBRARY_PATH", None)
    environment.pop("LD_PRELOAD", None)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory(prefix="frontend-smoke-") as temporary:
        for device, board in DEVICES.items():
            executable = package / "bin" / f"rx_gnss_{device}_frontend"
            dynamic = subprocess.check_output(["readelf", "-d", executable], text=True)
            for line in dynamic.splitlines():
                if "RUNPATH" in line or "RPATH" in line:
                    entries = re.search(r"\[(.*)\]", line)
                    if not entries or any(not entry.startswith("$ORIGIN/")
                                          for entry in entries[1].split(":")):
                        raise RuntimeError(f"Nonrelocatable runtime path: {line}")
            profiles = list((package / "share/rx_gnss/hardware" / board / "profiles").glob("*/frontend.json"))
            if not profiles:
                raise RuntimeError(f"No installed profiles for {device}")
            for profile in profiles:
                run([executable, "--check-config", profile, "--output", Path(temporary) / "capture.iq"],
                    cwd=temporary, env=environment, timeout=15)
            help_text = subprocess.check_output([executable, "--help"], text=True, env=environment)
            if "--demo" in help_text:
                run([executable, "--demo", "--screenshot", Path(temporary) / f"{device}.png"],
                    cwd=temporary, env=environment, timeout=15)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "build", "firmware", "package", "smoke"))
    parser.add_argument("--device", choices=(*DEVICES, "all"), default="all")
    parser.add_argument("--root", type=Path, default=ROOT / "build/hardware")
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 1, 8))
    parser.add_argument("--build-directory", type=Path, default=ROOT / "build/gcc-release")
    parser.add_argument("--output", type=Path, default=ROOT / "build/packages/frontends")
    parser.add_argument("--sdk-root", type=Path)
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.command == "package":
        package_frontends(args.output.resolve(), args.build_directory.resolve(), args.root.resolve())
    elif args.command == "smoke":
        smoke_package(args.output.resolve())
    else:
        for device in DEVICES if args.device == "all" else (args.device,):
            if args.command == "prepare":
                prepare(device, update=True)
            elif args.command == "build":
                build_host(device, args.root.resolve(), args.jobs)
            else:
                build_firmware(device, args.root.resolve(), args.jobs,
                               args.sdk_root.resolve() if args.sdk_root else None)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"hardware: {error}", file=sys.stderr)
        raise SystemExit(1)
