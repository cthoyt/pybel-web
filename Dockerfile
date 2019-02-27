FROM python:3.7
MAINTAINER Charles Tapley Hoyt "cthoyt@gmail.com"

RUN pip install --upgrade pip
RUN pip install psycopg2-binary gunicorn
RUN pip install git+https://github.com/pybel/pybel.git
RUN pip install git+https://github.com/pybel/pybel-tools.git

COPY . /app
WORKDIR /app
RUN pip install .
