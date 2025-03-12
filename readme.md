# Install application locally

Installation is pretty straightforward. You need to install requirements as you would do with any python package :
```
pip install -r requirements.txt
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

# Development

In development you want your CSS to be updated based on the tailwind classes you use in the HTML, to do that you need a process wathcing an re-compiling the css file.

The tailwindcss CLI can do that, the steps are:

1. Installing the node dependencies with `npm install`
2. Run the watch command on a separate shell (don't close the terminal, this need to be run in parallel of the launch script) 
```
npx @tailwindcss/cli -i ./static/input.css -o ./static/style.css --watch
```
