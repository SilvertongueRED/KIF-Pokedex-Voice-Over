FISH-SPEECH REFERENCE VOICE
===========================

Drop your Pokédex narrator reference clip here as:

    voice.wav     <- 10-30 second mono WAV (any sample rate, will be resampled)
    voice.txt     <- (optional) plain-text transcript of what's spoken in voice.wav

Fish-Speech clones whatever voice it hears in voice.wav.  For best results:

* Use a clean clip — no background music, no echo, no other speakers.
* 15-20 seconds is the sweet spot.
* Match the delivery you want at runtime (so use a clip of the narrator
  *narrating*, not the same voice in a different style).
* Include the transcript at voice.txt — it lets the model align the audio
  with the words and produces noticeably better clones.

Where do I get the clip?

* Rip ~20s of Dexter narration from the Pokémon anime (4Kids dub).
* Or generate one through the Fish Audio web UI:
    https://fish.audio/m/57a07a0af0954230a44d1db3adc77940/
  ...feed it a Pokédex-style sentence, click Generate, download the WAV.

Once voice.wav is in place, run:

    python ../setup.py

The setup script will normalise the clip (mono, 44.1 kHz, 16-bit PCM) and run
a smoke test to confirm the voice clones successfully.
