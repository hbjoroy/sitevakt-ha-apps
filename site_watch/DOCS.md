# Oppsett

Set observer til namnet på målestaden. Legg inn minst eitt mål under targets med name, url og check (ok, ready, grafana eller ha). Legg inn kanalen sitt integrasjonstoken i drift_token. Appen lagrar ventande rapportar i /data og sender til Sprøyt når tenesta svarar.

Ved overgang frå lokal app: kopier innstillingane med HA sitt YAML-val og ta backup av den lokale appen. Repository-utgåva har separat kø. Stopp den gamle når den nye leverer til kanalen. Behald den gamle appen så lenge ho har usende rapportar.
