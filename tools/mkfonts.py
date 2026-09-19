"""Fetch the webfonts once and write them into a stylesheet as data URIs.

Run by hand when the font stack changes; the output is committed so that
building the public page needs no network and no Google.
"""
import base64, re, subprocess, sys, pathlib

CSS_URL = ("https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700"
           "&family=Inter:wght@300;400;500"
           "&family=JetBrains+Mono:wght@400;500&display=swap")
# Latin only. The other subsets -- Cyrillic, Greek, Vietnamese -- are most
# of the 42 files and none of the characters this product renders.
KEEP = ("latin", "latin-ext")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def get(url: str) -> bytes:
    done = subprocess.run(
        ["curl", "-sS", "--fail", "--cacert", "/root/.ccr/ca-bundle.crt",
         "-A", UA, url],
        capture_output=True)
    if done.returncode != 0:
        sys.exit(f"could not fetch {url}: {done.stderr.decode()[:300]}")
    return done.stdout


css = get(CSS_URL).decode()
# Google emits "/* subset */" immediately before each @font-face block.
blocks = re.findall(r"/\* ([a-z-]+) \*/\s*(@font-face \{.*?\})", css, re.S)
out, kept, fetched = [], 0, {}
for subset, block in blocks:
    if subset not in KEEP:
        continue
    url = re.search(r"url\((https://[^)]+\.woff2)\)", block).group(1)
    if url not in fetched:
        fetched[url] = base64.b64encode(get(url)).decode()
    out.append(re.sub(
        r"url\(https://[^)]+\.woff2\)",
        f"url(data:font/woff2;base64,{fetched[url]})", block))
    kept += 1

header = (
    "/* Archivo, Inter and JetBrains Mono, embedded.\n"
    " *\n"
    " * Generated, not hand-written: see tools/mkfonts.py.\n"
    " *\n"
    " * The stylesheet used to @import these from fonts.googleapis.com,\n"
    " * which means every person who opens the page tells Google they\n"
    " * opened it, from their IP address, before a single word renders.\n"
    " * That is a poor trade on a page whose whole job is to be forwarded\n"
    " * to strangers. All three faces are under the SIL Open Font\n"
    " * Licence, which permits exactly this.\n"
    " *\n"
    " * Latin subsets only. The Cyrillic, Greek and Vietnamese cuts are\n"
    " * most of the bytes and none of the characters.\n"
    " */\n\n")

path = pathlib.Path("/home/user/CyberLogix-Ai/static/fonts-embedded.css")
path.write_text(header + "\n".join(out) + "\n")
print(f"{kept} face(s) from {len(fetched)} file(s) -> {path} "
      f"({path.stat().st_size // 1024} KB)")
