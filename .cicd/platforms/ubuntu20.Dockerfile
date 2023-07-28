FROM ubuntu:focal
ENV TZ="America/New_York"
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y build-essential      \
                       cmake                \
                       gcc-10               \
                       g++-10               \
                       git
RUN pip install -v "conan==1.60.2"
