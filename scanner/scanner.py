#!/usr/bin/env python3
"""
ArchPilot deterministic architecture scanner.

Designed to be imported by the Lambda analyzer and also run locally:
  python scanner.py /path/to/repository
"""

from __future__ import annotations

import json
import os
import re
import stat
import tarfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ARCH_X86 = "x86_64"
ARCH_ARM64 = "arm64"

EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".pytest_cache", "dist", "build", ".next", "target"
}

TEXT_FILE_NAMES = {
    "Dockerfile", "package.json", "package-lock.json", "npm-shrinkwrap.json",
    "requirements.txt", "pyproject.toml", "poetry.lock", "Pipfile",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock"
}

PLATFORM_RE = re.compile(r"\b(?:linux/)?(?:amd64|x86_64)\b", re.I)
EXPLICIT_ARM_RE = re.compile(r"\b(?:linux/)?arm64\b", re.I)
FROM_RE = re.compile(r"^\s*FROM(?:\s+--platform=(\S+))?\s+([^\s]+)", re.I | re.M)
EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+([0-9]+)", re.I | re.M)

ELF_MACHINE = {
    62: ARCH_X86,   # EM_X86_64
    183: ARCH_ARM64 # EM_AARCH64
}

def load_compatibility() -> Dict[str, Any]:
    path = Path(__file__).with_name("compatibility.json")
    return json.loads(path.read_text())

COMPAT = load_compatibility()

def safe_read_text(path: Path, limit: int = 1_000_000) -> str:
    try:
        raw = path.read_bytes()[:limit]
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return ""

def normalize_repo_root(root: str | Path) -> Path:
    root = Path(root).resolve()
    children = [p for p in root.iterdir() if p.name not in {"__MACOSX"}]
    dirs = [p for p in children if p.is_dir()]
    if len(dirs) == 1 and not (root / "Dockerfile").exists():
        nested = dirs[0]
        if any((nested / name).exists() for name in ("Dockerfile", "package.json", "requirements.txt", "go.mod", "pyproject.toml")):
            return nested
    return root

def iter_files(root: Path) -> Iterable[Path]:
    count = 0
    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIRS for part in rel_parts):
            continue
        if path.is_file():
            yield path
            count += 1
            if count >= 20_000:
                return

def is_elf(data: bytes) -> bool:
    return len(data) >= 20 and data[:4] == b"\x7fELF"

def elf_arch(path: Path) -> Optional[str]:
    try:
        data = path.read_bytes()[:64]
    except Exception:
        return None
    if not is_elf(data):
        return None
    # e_machine is bytes 18:20, little-endian for normal Linux ELF files.
    machine = int.from_bytes(data[18:20], "little")
    return ELF_MACHINE.get(machine)

def base_image_support(image: str) -> Tuple[Optional[bool], str]:
    image = image.split("@", 1)[0]
    for prefix in COMPAT["arm64_safe_base_images"]:
        if image == prefix or image.startswith(prefix + ":") or image.startswith(prefix + "/"):
            return True, f"Base image '{image}' is on the curated ARM64-safe allowlist."
    if image.startswith("scratch"):
        return True, "scratch has no architecture-specific userland."
    return None, f"Base image '{image}' is not in the curated ARM64 allowlist."

def parse_dockerfile(root: Path, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    dockerfile = root / "Dockerfile"
    if not dockerfile.exists():
        findings.append({
            "type": "dockerfile",
            "severity": "hard",
            "message": "No root Dockerfile found; ArchPilot MVP expects a containerized repository."
        })
        return {"exists": False, "baseImages": [], "containerPort": 8080}

    text = safe_read_text(dockerfile)
    base_images = []
    unknown_base = False

    for platform, image in FROM_RE.findall(text):
        support, message = base_image_support(image)
        base_images.append({
            "image": image,
            "explicitPlatform": platform,
            "arm64Support": support,
            "message": message
        })
        if support is None:
            unknown_base = True
            findings.append({
                "type": "base_image",
                "severity": "ambiguous",
                "evidence": image,
                "message": message
            })
        elif support is False:
            findings.append({
                "type": "base_image",
                "severity": "hard",
                "evidence": image,
                "message": message
            })

    if PLATFORM_RE.search(text):
        # Only treat it as hard if it is actually an explicit platform lock.
        hard_lock = bool(re.search(r"--platform\s*=\s*(?:linux/)?(?:amd64|x86_64)", text, re.I))
        if hard_lock:
            findings.append({
                "type": "architecture_lock",
                "severity": "hard",
                "message": "Dockerfile explicitly locks the build to amd64/x86_64."
            })

    expose = EXPOSE_RE.findall(text)
    port = int(expose[0]) if expose else 8080

    return {
        "exists": True,
        "baseImages": base_images,
        "unknownBaseImage": unknown_base,
        "containerPort": port
    }

def detect_dependencies(root: Path, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = {
        "ecosystems": [],
        "nativeAddons": [],
        "knownX86Only": []
    }

    package_json = root / "package.json"
    if package_json.exists():
        result["ecosystems"].append("node")
        try:
            package_data = json.loads(package_json.read_text())
        except Exception:
            package_data = {}
        deps = {}
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            deps.update(package_data.get(section, {}) or {})

        if (root / "binding.gyp").exists():
            result["nativeAddons"].append("binding.gyp")
            findings.append({
                "type": "native_dependency",
                "severity": "ambiguous",
                "evidence": "binding.gyp",
                "message": "Node native addon build detected."
            })

        for package_name in deps:
            if package_name in COMPAT["known_x86_only_packages"]["node"]:
                result["knownX86Only"].append(package_name)
                findings.append({
                    "type": "dependency",
                    "severity": "hard",
                    "evidence": package_name,
                    "message": "Package is on the curated x86-only list."
                })

    req = root / "requirements.txt"
    pyproject = root / "pyproject.toml"
    if req.exists() or pyproject.exists():
        result["ecosystems"].append("python")
        text = safe_read_text(req) + "\n" + safe_read_text(pyproject)
        for package_name in COMPAT["known_x86_only_packages"]["python"]:
            if re.search(rf"(?im)^\s*{re.escape(package_name)}(?:[<=>!\s]|$)", text):
                result["knownX86Only"].append(package_name)
                findings.append({
                    "type": "dependency",
                    "severity": "hard",
                    "evidence": package_name,
                    "message": "Package is on the curated x86-only list."
                })

    if (root / "go.mod").exists():
        result["ecosystems"].append("go")
        go_text = safe_read_text(root / "go.mod")
        docker = safe_read_text(root / "Dockerfile")
        if "CGO_ENABLED=1" in docker or "CGO_ENABLED=1" in go_text:
            findings.append({
                "type": "native_linking",
                "severity": "ambiguous",
                "evidence": "CGO_ENABLED=1",
                "message": "Go CGO/native linking detected; architecture compatibility needs validation."
            })

    if (root / "Cargo.toml").exists():
        result["ecosystems"].append("rust")

    return result

def scan_binaries(root: Path, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    binaries = []
    candidates = 0
    for path in iter_files(root):
        if candidates >= 2500:
            break
        suffix = path.suffix.lower()
        try:
            mode = path.stat().st_mode
        except Exception:
            continue
        if suffix in {".so", ".bin", ".elf"} or bool(mode & stat.S_IXUSR):
            candidates += 1
            arch = elf_arch(path)
            if arch:
                item = {
                    "path": str(path.relative_to(root)),
                    "architecture": arch
                }
                binaries.append(item)
                if arch == ARCH_X86:
                    findings.append({
                        "type": "native_binary",
                        "severity": "hard",
                        "evidence": item["path"],
                        "message": "Committed ELF binary is x86_64."
                    })
                elif arch == ARCH_ARM64:
                    findings.append({
                        "type": "native_binary",
                        "severity": "compatible",
                        "evidence": item["path"],
                        "message": "Committed ELF binary is ARM64."
                    })
    return binaries

def scan_architecture_references(root: Path, findings: List[Dict[str, Any]]) -> List[str]:
    matches = []
    interesting_suffixes = {".yml", ".yaml", ".sh", ".bash", ".ps1", ".json", ".toml", ".md", ".txt", ".Dockerfile", ""}
    for path in iter_files(root):
        if path.name not in TEXT_FILE_NAMES and path.suffix.lower() not in interesting_suffixes:
            continue
        text = safe_read_text(path, limit=300_000)
        if re.search(r"--platform(?:=|\s+)(?:linux/)?(?:amd64|x86_64)", text, re.I):
            rel = str(path.relative_to(root))
            matches.append(rel)
            findings.append({
                "type": "architecture_reference",
                "severity": "hard",
                "evidence": rel,
                "message": "Architecture-specific amd64/x86_64 reference found."
            })
    return matches

def compute_verdict(
    findings: List[Dict[str, Any]],
    docker_info: Dict[str, Any]
) -> Tuple[str, float, Dict[str, int]]:
    """
    Determine architecture compatibility from scanner evidence.

    Scores are evidence indicators, not probabilities:
      - x86_64 score represents hard x86 evidence.
      - arm64 score represents positive ARM64 evidence.
      - ambiguous score represents evidence requiring further validation.
    """

    scores = {
        "arm64": 0,
        "x86_64": 0,
        "ambiguous": 0,
    }

    for finding in findings:
        finding_type = finding.get("type")
        severity = finding.get("severity")

        if finding_type == "native_binary":
            if severity == "hard":
                scores["x86_64"] += 100

        elif severity == "compatible":
            scores["arm64"] += 30

        elif finding_type == "architecture_lock":
            scores["x86_64"] += 100

        elif finding_type == "dependency":
            if severity == "hard":
                scores["x86_64"] += 100

        elif finding_type == "base_image":
            if severity == "ambiguous":
                scores["ambiguous"] += 20

        elif finding_type == "native_dependency":
            scores["ambiguous"] += 15

        elif finding_type == "native_linking":
            scores["ambiguous"] += 15

        elif finding_type == "architecture_reference":
            scores["x86_64"] += 100

    # Positive ARM64 evidence from known-safe Docker base images.
    for image in docker_info.get("baseImages", []):
        if image.get("arm64Support") is True:
            scores["arm64"] += 15

    # No Dockerfile means the current MVP cannot deploy the repository.
    if not docker_info.get("exists"):
        return "unsupported", 1.0, scores        

    # Hard x86 evidence always takes precedence.
    if scores["x86_64"] > 0:
        confidence = min(0.99, 0.90 + (scores["x86_64"] / 1000))
        return "x86_required", round(confidence, 2), scores

    # Ambiguous evidence should be handed to the reasoning layer later.
    if scores["ambiguous"] > 0:
        confidence = max(
            0.50,
            min(0.85, 0.50 + (scores["ambiguous"] / 100))
        )
        return "ambiguous", round(confidence, 2), scores

    # Clean repository with positive ARM64 evidence.
    if scores["arm64"] > 0:
        confidence = min(
            0.99,
            0.90 + (scores["arm64"] / 1000)
        )
        return "native_arm64", round(confidence, 2), scores

    # No positive or negative architecture evidence.
    return "ambiguous", 0.50, scores

def scan_repository(root: str | Path) -> Dict[str, Any]:
    root = normalize_repo_root(root)
    findings: List[Dict[str, Any]] = []
    docker_info = parse_dockerfile(root, findings)
    deps = detect_dependencies(root, findings)
    binaries = scan_binaries(root, findings)
    arch_refs = scan_architecture_references(root, findings)
    verdict, confidence, architecture_score = compute_verdict(findings, docker_info)

    summary = {
        "nativeArm64": sum(1 for f in findings if f["severity"] == "compatible"),
        "ambiguous": sum(1 for f in findings if f["severity"] == "ambiguous"),
        "hardFailures": sum(1 for f in findings if f["severity"] == "hard")
    }

    return {
        "verdict": verdict,
        "confidence": confidence,
        "architectureScore": architecture_score,
        "scannerVersion": "0.1.0",
        "docker": docker_info,
        "dependencies": deps,
        "nativeBinaries": binaries,
        "architectureReferences": arch_refs,
        "findings": findings,
        "summary": summary
    }

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    args = parser.parse_args()
    print(json.dumps(scan_repository(args.repo), indent=2))

if __name__ == "__main__":
    main()
