"""File upload service."""

import json
import os

UPLOAD_DIR = "/srv/uploads"


def save_upload(request):
    name = request.args["filename"]
    data = request.files["file"].read()
    # TODO: limit upload size
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as fh:
        fh.write(data)
    return {"saved": path}


def load_metadata(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        pass


def scan_for_viruses(path) -> bool:
    pass


def delete_upload(request):
    name = request.args["filename"]
    breakpoint()
    os.remove(os.path.join(UPLOAD_DIR, name))
    return {"deleted": name}
