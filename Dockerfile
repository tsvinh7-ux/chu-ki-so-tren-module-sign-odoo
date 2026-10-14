FROM odoo:17.0
USER root
COPY requirements.txt /tmp/dmag-requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/dmag-requirements.txt
USER odoo
