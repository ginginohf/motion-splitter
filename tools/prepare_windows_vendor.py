"""Download a checksummed upstream Windows bundle for development acceptance."""
import hashlib,json,shutil,subprocess,urllib.request,zipfile
from pathlib import Path
url='https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip'
root=Path('build/windows-download');root.mkdir(parents=True,exist_ok=True)
archive=root/'ffmpeg.zip'
expected=urllib.request.urlopen(url+'.sha256').read().decode().split()[0]
urllib.request.urlretrieve(url,archive)
assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected
with zipfile.ZipFile(archive) as z:z.extractall(root/'unpacked')
source=next((root/'unpacked').glob('*'))
vendor=Path('build/vendor-windows');notices=vendor/'notices';notices.mkdir(parents=True)
for name in ['ffmpeg.exe','ffprobe.exe']:shutil.copy2(source/'bin'/name,vendor/name)
license_file=next(source.glob('LICENSE*'));shutil.copy2(license_file,notices/'FFmpeg.txt')
for p in source.glob('README*'):shutil.copy2(p,notices/p.name)
(notices/'SOURCES.txt').write_text('Upstream Windows bundle: '+url+'\nArchive SHA256: '+expected+'\nFFmpeg sources: https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz\nBuild provenance: https://www.gyan.dev/ffmpeg/builds/\nDevelopment acceptance only; complete source/dependency redistribution review pending.\n')
(notices/'DEPENDENCIES.txt').write_text('Static GPLv3 essentials build from Gyan. Consult included upstream README and configuration below. Python and native dependency redistribution review remains pending.\n')
config=subprocess.check_output([str(vendor/'ffmpeg.exe'),'-version'],text=True)
(notices/'configuration.txt').write_text(config)
manifest=dict(target='windows-x64',ffmpeg_version='8.1.2',origin_url=url,source_url='https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz',configuration=config,redistribution_reviewed=False,files={p.relative_to(vendor).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in vendor.rglob('*') if p.is_file()})
(vendor/'manifest.json').write_text(json.dumps(manifest,indent=2))
