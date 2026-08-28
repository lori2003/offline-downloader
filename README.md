# Vixcloud Offline Downloader

Downloader minimale per conservare offline un singolo video Vixcloud tramite
GitHub Actions. È pensato esclusivamente per contenuti propri, autorizzati o di
pubblico dominio; non aggira DRM, login o controlli di accesso.

## Uso da GitHub

1. Apri **Actions → Scarica video Vixcloud**.
2. Premi **Run workflow**.
3. Incolla un URL `vixcloud.co` del player o una playlist `.m3u8`.
4. Spunta la conferma relativa ai diritti e avvia il workflow.
5. Apri l'esecuzione e poi il passaggio **Scarica il video**: durante il lavoro
   mostra percentuale, quantità scaricata, velocità e tempo residuo.
6. Al termine scarica l'artefatto `video-vixcloud-*` in fondo al riepilogo.

L'artefatto resta disponibile per 3 giorni. La pagina GitHub Pages inclusa nella
repo copia l'URL e apre direttamente il workflow corretto; un sito statico non
può avviare Actions al posto dell'utente senza esporre un token GitHub.

## Uso locale

Servono Python 3.10 o successivo e `ffmpeg` disponibile nel `PATH`.

```bash
python -m pip install -r requirements.txt
python download.py "https://vixcloud.co/..." --output-dir downloads
```

Il programma:

- accetta solo `vixcloud.co` e relativi sottodomini;
- riconosce i parametri temporanei presenti nel player Vixcloud;
- rimuove ritorni a capo letterali o `%0A` inseriti accidentalmente copiando il link;
- preserva correttamente i parametri già presenti nella playlist;
- usa `yt-dlp` come downloader HLS con tentativi automatici;
- presenta le richieste come un browser Chrome e usa un Referer Vixcloud valido;
- mostra un avanzamento compatto nei log e un riepilogo finale verde o rosso;
- maschera URL e token nei nuovi log di Actions.

## Limiti e risoluzione problemi

- Usa un link fresco del player: i link firmati possono scadere rapidamente.
- Una risposta `403` può indicare che Vixcloud blocca l'indirizzo del runner
  GitHub. In quel caso riprova localmente dalla connessione autorizzata.
- GitHub applica limiti di durata, spazio e dimensione agli artefatti. Per file
  molto grandi è più affidabile l'uso locale.
- Non inserire cookie, password o URL contenenti credenziali: la repo e i log di
  Actions sono pubblici.
- Se il contenuto richiede DRM, un account o un'altra protezione di accesso, il
  downloader si ferma.

## Test

I test non contattano Vixcloud e non scaricano film; usano solo una pagina player
sintetica.

```bash
python -m unittest discover -s tests -v
```
