# 1. Base image (Must match the python version in amvera.yml)
FROM python:3.10-alpine 

# 2. Set the working directory inside the container
WORKDIR /app

# 3. Copy requirements and install dependencies
# This ensures caching works and dependencies are installed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy all remaining files (bot.py, etc.) to the working directory
# Note: The destination is '.' which implies copying to the current working directory /app
COPY . . 

# 5. Define the command that runs the worker
CMD ["python", "bot.py"]
