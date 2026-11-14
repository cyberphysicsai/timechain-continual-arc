"""Controller-only reset evidence, collected before the learner process starts."""
import hashlib
import json
import os
import stat

from continual_arc_baselines.systems.hosted import WRITABLE


def snapshot_digest(base):
    """Hash every owned writable entry without following symlinks.

    Coverage: topology, modes/owners, mtimes, symlink targets, readable xattrs and
    regular-file bytes. Access times, inode identities and creation times are
    deliberately excluded. No file content or path listing enters the receipt.
    """
    digest = hashlib.sha256()
    entries = files = total_bytes = 0
    stack = [(base / name, name) for name in reversed(WRITABLE)]
    while stack:
        path, relative = stack.pop()
        info = path.lstat()
        header = {'path': relative, 'mode': info.st_mode, 'uid': info.st_uid,
                  'gid': info.st_gid, 'mtime_ns': info.st_mtime_ns}
        attrs = []
        if hasattr(os, 'listxattr'):
            for name in sorted(os.listxattr(path, follow_symlinks=False)):
                value = os.getxattr(path, name, follow_symlinks=False)
                attrs.append((name, hashlib.sha256(value).hexdigest()))
        header['xattrs'] = attrs
        entries += 1
        if stat.S_ISLNK(info.st_mode):
            header['link'] = os.readlink(path)
        elif stat.S_ISDIR(info.st_mode):
            children = sorted(path.iterdir(), key=lambda p: p.name, reverse=True)
            stack.extend((child, relative + '/' + child.name) for child in children)
        elif stat.S_ISREG(info.st_mode):
            files += 1
            total_bytes += info.st_size
            content = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            try:
                fd = os.open(path, flags | getattr(os, 'O_NOATIME', 0))
            except PermissionError:
                fd = os.open(path, flags)
            with os.fdopen(fd, 'rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    content.update(chunk)
            header.update(size=info.st_size, content_sha256=content.hexdigest())
        else:
            raise RuntimeError('Unsupported entry in a frozen writable snapshot')
        digest.update(json.dumps(header, sort_keys=True, separators=(',', ':'),
                                 ensure_ascii=True).encode() + b'\n')
    return {'sha256': digest.hexdigest(), 'entries': entries,
            'regular_files': files, 'regular_file_bytes': total_bytes,
            'hash_spec': 'paths,types,modes,owners,mtime_ns,link_targets,xattrs,regular_bytes'}
