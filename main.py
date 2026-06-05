from fastapi import FastAPI, UploadFile, Form
from fastapi.responses import FileResponse
import os
import json

app = FastAPI()

os.makedirs("storage", exist_ok=True)

open("storage/files.json", "a").close()

def loadFiles():
    try:
        return json.load(open("storage/files.json", "r"))
    except:
        return []

def saveFiles(files):
    json.dump(files, open("storage/files.json", "w"))

@app.post("/upload")
def uploadFile(file: UploadFile, username: str = Form()):
    content = file.file.read()
    with open(f"storage/{file.filename}", "wb") as f:
        f.write(content)
    files = loadFiles()
    files.append(
        {
            "file": file.filename,
            "user": username
        }
    )
    saveFiles(files)
    return({"status": "ok"})


@app.get("/files")
def getFiles(username: str):
    files = loadFiles()
    for i in range(len(files)):
        if files[i]["user"] == username:
            files.append(files[i])
    return files


@app.get("/download/{filename}")
def downloadFile(filename: str):
    return FileResponse(f"storage/{filename}", media_type="application/octet-stream", filename=filename)

