FROM python:3.9

WORKDIR /usr/src/app

COPY requirements.txt ./

RUN pip install --no-cache-dir --upgrade -r requirements.txt
COPY . .

ENV PYTHONPATH "${PYTHONPATH}:/usr/src/app"

ENV FREEIPA_HOST=your-freeipa.host.com
ENV GOOGLE_AUTH_FILE_PATH=path/to/your/google-auth-file.json
ENV GOOGLE_SHEETS_DOC_KEY=your-google-sheet-doc-key

CMD ["python", "./app.py"]
