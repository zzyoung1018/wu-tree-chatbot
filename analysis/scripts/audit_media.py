"""古树资料包审计：逐文件生成清单（格式、EXIF/GPS、清晰度、重复）并标记质量问题。

用法:
    python audit_media.py <解压根目录> <输出目录> [--expected-year 2026]

<解压根目录> 下每个一级子目录视为一棵树/一个点位（例如每个 zip 解压到一个子目录）。
输出 inventory.json 与 inventory.csv（UTF-8 BOM，可直接用 Excel 打开）。

依赖: pip install pillow imagehash numpy ；视频分析需要系统已安装 ffprobe（随 ffmpeg 提供）。
"""
import argparse
import csv
import hashlib
import json
import os
import re
import subprocess

import imagehash
import numpy as np
from PIL import ExifTags, Image

Image.MAX_IMAGE_PIXELS = None

ap = argparse.ArgumentParser()
ap.add_argument("root")
ap.add_argument("out")
ap.add_argument("--expected-year", help="本批调查年份；视频创建年份与之不同时标记")
args = ap.parse_args()
ROOT, OUT = args.root, args.out
TREE_OF = {}  # 可选：一级目录名 -> 显示名；为空时直接使用目录名

MAGIC = [
    (b"\xff\xd8\xff", "jpeg"), (b"\x89PNG", "png"), (b"PK\x03\x04", "zip/ooxml"),
    (b"%PDF", "pdf"), (b"GIF8", "gif"),
]


def sniff(path):
    with open(path, "rb") as f:
        head = f.read(16)
    for sig, name in MAGIC:
        if head.startswith(sig):
            return name
    if head[4:8] == b"ftyp":
        return "mp4/mov"
    return "unknown" if head else "empty"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dms_to_deg(v, ref):
    d, m, s = (float(x) for x in v)
    deg = d + m / 60 + s / 3600
    return -deg if ref in ("S", "W") else deg


def exif_info(im):
    info = {}
    ex = im.getexif()
    if not ex:
        return info
    tags = {ExifTags.TAGS.get(k, k): v for k, v in ex.items()}
    for k in ("Make", "Model", "Software", "Orientation", "DateTime", "Artist", "Copyright", "ImageDescription"):
        if k in tags:
            info[k] = str(tags[k]).strip("\x00 ")
    sub = ex.get_ifd(0x8769)
    for k, v in sub.items():
        name = ExifTags.TAGS.get(k, k)
        if name in ("DateTimeOriginal", "OffsetTimeOriginal", "FocalLength", "FNumber", "ExposureTime", "ISOSpeedRatings", "LensModel"):
            info[name] = str(v).strip("\x00 ")
    gps = ex.get_ifd(0x8825)
    if gps:
        g = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}
        try:
            info["lat"] = round(dms_to_deg(g["GPSLatitude"], g.get("GPSLatitudeRef", "N")), 7)
            info["lon"] = round(dms_to_deg(g["GPSLongitude"], g.get("GPSLongitudeRef", "E")), 7)
        except Exception:
            pass
        if "GPSAltitude" in g:
            info["alt"] = round(float(g["GPSAltitude"]), 1)
    return info


def xmp_info(path):
    """DJI drones write flight/gimbal data into XMP."""
    with open(path, "rb") as f:
        data = f.read(200_000)
    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", data, re.S)
    if not m:
        return {}
    x = m.group(0).decode("utf-8", "ignore")
    out = {}
    for key in ("RelativeAltitude", "AbsoluteAltitude", "GimbalPitchDegree", "FlightYawDegree", "GpsLatitude", "GpsLongitude", "GpsLongtitude"):
        mm = re.search(r'drone-dji:%s="([^"]+)"' % key, x)
        if mm:
            out["dji_" + key] = mm.group(1)
    mm = re.search(r'photoshop:DateCreated="([^"]+)"|xmp:CreatorTool="([^"]+)"', x)
    if mm:
        out["xmp_tool_or_date"] = mm.group(1) or mm.group(2)
    return out


def quality(im):
    g = im.convert("L")
    g.thumbnail((1024, 1024))
    a = np.asarray(g, dtype=np.float32)
    lap = (a[1:-1, 1:-1] * -4 + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:])
    return {
        "sharpness_lapvar": round(float(lap.var()), 1),
        "mean_luma": round(float(a.mean()), 1),
        "clip_dark_pct": round(float((a < 8).mean() * 100), 2),
        "clip_bright_pct": round(float((a > 247).mean() * 100), 2),
    }


def ffprobe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    j = json.loads(r.stdout or "{}")
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = j.get("format", {})
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    return {
        "width": v.get("width"), "height": v.get("height"),
        "codec": f'{v.get("codec_name")} {v.get("profile")} {v.get("pix_fmt")}',
        "fps": round(int(num) / int(den), 2) if int(den) else None,
        "duration_s": round(float(fmt.get("duration", 0)), 1),
        "bitrate_mbps": round(int(fmt.get("bit_rate", 0)) / 1e6, 1),
        "has_audio": a is not None,
        "created": fmt.get("tags", {}).get("creation_time"),
        "encoder": fmt.get("tags", {}).get("encoder"),
    }


NOTE_IN_NAME = re.compile(r"[（(].+[)）]|，|是.+不是|推测|可能|备注")
CAMERA_RAW_NAME = re.compile(r"^(DSC|DJI|IMG|_DSC|P\d|\d{10,})|\.pic_hd|^[0-9A-F]{16}\.", re.I)

rows = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames.sort()
    for fn in sorted(filenames):
        p = os.path.join(dirpath, fn)
        rel = os.path.relpath(p, ROOT)
        key = rel.split(os.sep)[0]
        ext = os.path.splitext(fn)[1].lower()
        size = os.path.getsize(p)
        real = sniff(p)
        r = {
            "tree": TREE_OF.get(key, key), "path": rel, "name": fn, "ext": ext,
            "size_mb": round(size / 1e6, 2), "real_format": real, "sha256": sha256(p),
            "depth": rel.count(os.sep), "issues": [],
        }
        if size == 0:
            r["issues"].append("0字节文件(内容写在文件名里)")
        if real in ("jpeg", "png"):
            with Image.open(p) as im:
                r["width"], r["height"] = im.size
                r["megapixels"] = round(im.size[0] * im.size[1] / 1e6, 1)
                r.update(exif_info(im))
                r.update(quality(im))
                r["phash"] = str(imagehash.phash(im.convert("RGB")))
            r.update(xmp_info(p))
            if ext in (".jpg", ".jpeg") and real != "jpeg":
                r["issues"].append(f"扩展名{ext}与真实格式{real}不符")
            if not r.get("Make"):
                r["issues"].append("无EXIF(相机/时间被剥离,疑似微信/编辑软件导出)")
            if "lat" not in r:
                r["issues"].append("无GPS")
            if r.get("sharpness_lapvar", 1e9) < 60:
                r["issues"].append("清晰度偏低(拉普拉斯方差<60)")
            if r.get("clip_bright_pct", 0) > 5 or r.get("clip_dark_pct", 0) > 5:
                r["issues"].append("曝光裁切>5%")
            if r["size_mb"] > 15:
                r["issues"].append("单图>15MB,需派生Web版本")
        elif real == "mp4/mov":
            r.update(ffprobe(p))
            if r["size_mb"] > 100:
                r["issues"].append("视频>100MB,需转码/切片")
            if "hevc" in (r.get("codec") or "") and "10" in (r.get("codec") or ""):
                r["issues"].append("HEVC 10bit,浏览器兼容性差")
            if r.get("has_audio"):
                r["issues"].append("含音轨(需检查人声/隐私)")
            year = (r.get("created") or "")[:4]
            if args.expected_year and year and year != args.expected_year:
                r["issues"].append(f"创建于{year}年(非本批{args.expected_year}调查)")
        if NOTE_IN_NAME.search(os.path.splitext(fn)[0]):
            r["issues"].append("文件名中嵌入了说明/推测性文字")
        if CAMERA_RAW_NAME.search(fn):
            r["issues"].append("原始相机/微信文件名,无语义")
        if ext != ext.lower() or fn.endswith((".JPG", ".MP4")):
            r["issues"].append("扩展名大小写不统一")
        rows.append(r)

# exact and perceptual duplicates
by_hash = {}
for r in rows:
    by_hash.setdefault(r["sha256"], []).append(r["path"])
for r in rows:
    dup = [x for x in by_hash[r["sha256"]] if x != r["path"]]
    if dup and r["size_mb"] > 0:
        r["issues"].append("完全重复: " + "; ".join(dup))

imgs = [r for r in rows if "phash" in r]
near = []
for i in range(len(imgs)):
    for j in range(i + 1, len(imgs)):
        d = imagehash.hex_to_hash(imgs[i]["phash"]) - imagehash.hex_to_hash(imgs[j]["phash"])
        if d <= 10:
            near.append((d, imgs[i]["path"], imgs[j]["path"]))
for d, a, b in near:
    for r in imgs:
        if r["path"] in (a, b):
            other = b if r["path"] == a else a
            r["issues"].append(f"近似重复(pHash距离{d}): {os.path.basename(other)}")

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "inventory.json"), "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, indent=1)

cols = ["tree", "path", "real_format", "ext", "size_mb", "width", "height", "megapixels", "Make", "Model",
        "DateTimeOriginal", "lat", "lon", "alt", "dji_RelativeAltitude", "dji_GimbalPitchDegree",
        "codec", "fps", "duration_s", "bitrate_mbps", "has_audio", "created",
        "sharpness_lapvar", "mean_luma", "clip_dark_pct", "clip_bright_pct", "phash", "sha256", "issues"]
with open(os.path.join(OUT, "inventory.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "issues": " | ".join(r["issues"])})

# console summary
for r in rows:
    meta = []
    for k in ("width", "height", "Model", "DateTimeOriginal", "lat", "lon", "alt", "dji_RelativeAltitude",
              "dji_GimbalPitchDegree", "sharpness_lapvar", "mean_luma", "codec", "duration_s", "created"):
        if r.get(k) not in (None, ""):
            meta.append(f"{k}={r[k]}")
    print(f"[{r['tree']}] {r['path']} ({r['size_mb']}MB, {r['real_format']})")
    print("    " + ", ".join(meta))
    for iss in r["issues"]:
        print("    ! " + iss)
print("\nnear-duplicate pairs:", len(near))
for d, a, b in sorted(near):
    print(f"  d={d}: {os.path.basename(a)} <-> {os.path.basename(b)}")
