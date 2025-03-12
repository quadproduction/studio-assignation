# Install application locally

Installation is pretty straightforward. You need to install requirements as you would do with any python package :
```
pip install -r requirements.txt
```


# Install application in Docker

You can follow [FastAPI guide](https://fastapi.tiangolo.com/deployment/docker/) to deploy application in docker container.

To summarize the installation, you have to type the two following command lines :
```
docker build -t projet_moore .
docker run -d --name projet_moore -p 80:80 projet_moore
```

The application will be then available at defined port.

# Launch application

After installation, you just have to launch the given execution script to start the website. It will be available at `http://localhost:5000`

## Windows

```
./launch.bat
```

## Linux

```
./launch.sh
```