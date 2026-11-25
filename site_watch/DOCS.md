# Oppsett

Set observer til namnet på målestaden. Legg inn minst eitt mål under targets med name, url og check (ok, ready, grafana eller ha). Legg inn kanalen sitt integrasjonstoken i drift_token. Appen lagrar ventande rapportar i /data og sender til Sprøyt når tenesta svarar.

Ved overgang frå lokal app: kopier innstillingane med HA sitt YAML-val og ta backup av den lokale appen. Repository-utgåva har separat kø. Stopp den gamle når den nye leverer til kanalen. Behald den gamle appen så lenge ho har usende rapportar.

## Lys på dashboardet

Med Mosquitto og MQTT-integrasjonen i Home Assistant får kvar målestad ei eining som heiter **Sitevakt** etterfølgt av namnet på målestaden. Sitevakt hentar tilkoplinga frå Supervisor og brukar standardprefikset `homeassistant` for MQTT discovery.

Opne **Innstillingar → Einingar og tenester → MQTT** og finn Sitevakt. **Tenestefeil** blir aktiv når minst eitt av måla har feila tre kontrollar på rad. Kvar teneste har òg sin eigen problemsensor. Når ei teneste svarar att, blir feilen klarert på første vellukka kontroll. Etter tre minutt utan nye målingar blir sensorane utilgjengelege.

På dashboardet vel du **Rediger → Legg til kort → Tile**, vel **Tenestefeil** og set fargen til raud. Du kan leggje til sensorane for kvar teneste på same måte.
