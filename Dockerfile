FROM dawn001/z_mirror:hk_latest

WORKDIR /usr/src/app
RUN chmod 777 /usr/src/app

RUN pip3 uninstall pyrofork -y && pip3 install git+https://github.com/Hrishi2861/pyrofork-2.2.11-peer-fix.git

COPY . .

CMD ["bash", "start.sh"]
