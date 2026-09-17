from decimal import Decimal
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import boto3

from scanner import scan_repository

s3 = boto3.client("s3")
ddb = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])

MAX_ARCHIVE_BYTES = 40 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024

def ddb_value(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value

def update_job(job_id, **values):
    expr = []
    names = {}
    vals = {}
    for key, value in values.items():
        attr = f"#{key}"
        val = f":{key}"
        names[attr] = key
        vals[val] = ddb_value(value)
        expr.append(f"{attr} = {val}")
    ddb.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET " + ", ".join(expr),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )

def validate_github_url(repo_url: str):
    match = re.fullmatch(r"https://github\.com/([^/]+)/([^/#?]+?)(?:\.git)?/?", repo_url.strip())
    if not match:
        raise ValueError("Only public GitHub repository URLs are supported.")
    return match.group(1), match.group(2)

def github_json(url: str):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ArchPilot/0.1",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

def download_archive(owner: str, repo: str, branch: str, destination: Path):
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{branch}"
    request = urllib.request.Request(url, headers={"User-Agent": "ArchPilot/0.1"})
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Repository archive exceeds ArchPilot's 40 MB MVP limit.")
            out.write(chunk)

def safe_extract(tar_path: Path, destination: Path):
    total = 0
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise ValueError("Unsafe archive path detected.")
            # Do not extract symlinks, hard links, devices, or other special files.
            if not (member.isfile() or member.isdir()):
                continue
            total += int(member.size or 0)
            if total > MAX_EXTRACTED_BYTES:
                raise ValueError("Repository exceeds ArchPilot's 200 MB extracted MVP limit.")
            target = (destination / name).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise ValueError("Archive member escapes extraction directory.")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            with target.open("wb") as out:
                while True:
                    chunk = extracted.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)

def lambda_handler(event, context):
    job_id = event["jobId"]
    repo_url = event["repoUrl"]

    update_job(job_id, status="RUNNING", stage="SCANNING")

    owner, repo = validate_github_url(repo_url)
    metadata = github_json(f"https://api.github.com/repos/{owner}/{repo}")
    if metadata.get("visibility") != "public":
        raise ValueError("Repository must be public for this MVP.")

    branch = metadata["default_branch"]

    work_dir = Path(tempfile.mkdtemp(prefix="archpilot-", dir="/tmp"))
    archive = work_dir / "repo.tgz"
    extract_dir = work_dir / "extract"
    extract_dir.mkdir()

    try:
        download_archive(owner, repo, branch, archive)
        safe_extract(archive, extract_dir)

        extracted_children = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(extracted_children) == 1:
            repo_root = extracted_children[0]
        else:
            repo_root = extract_dir

        result = scan_repository(repo_root)

        scan_key = f"jobs/{job_id}/scanner.json"
        s3.put_object(
            Bucket=os.environ["REPORTS_BUCKET"],
            Key=scan_key,
            Body=json.dumps(result, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
        )

        update_job(
            job_id,
            status="ANALYZED",
            stage="DECISION",
            verdict=result["verdict"],
            confidence=result["confidence"],
            containerPort=result["docker"]["containerPort"],
            scanKey=scan_key,
        )

        return {
            **event,
            "analysis": result,
            "scanKey": scan_key,
            "containerPort": result["docker"]["containerPort"],
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
