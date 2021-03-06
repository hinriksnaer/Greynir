#!/usr/bin/env python
"""

    Greynir: Natural language processing for Icelandic

    Copyright (C) 2020 Miðeind ehf.

       This program is free software: you can redistribute it and/or modify
       it under the terms of the GNU General Public License as published by
       the Free Software Foundation, either version 3 of the License, or
       (at your option) any later version.
       This program is distributed in the hope that it will be useful,
       but WITHOUT ANY WARRANTY; without even the implied warranty of
       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
       GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see http://www.gnu.org/licenses/.


    Icelandic text to speech via AWS Polly.

"""

import sys
import os
import json
import logging

import cachetools
import boto3
from botocore.exceptions import ClientError

# The AWS Polly API access keys (you must obtain your own keys if you want to use this code)
# JSON format is the following:
# {
#     "aws_access_key_id": ""my_key,
#     "aws_secret_access_key": "my_secret",
#     "region_name": "my_region"
# }
#
_API_KEYS_PATH = os.path.join("resources", "aws_polly_keys.mideind.json")
_api_client = None

# Voices
_DEFAULT_VOICE = "Dora"
_VOICES = frozenset(("Dora", "Karl"))

# Audio formats
_DEFAULT_AUDIO_FORMAT = "mp3"
_AUDIO_FORMATS = frozenset(("mp3", "ogg_vorbis", "pcm"))

# Text formats
# For details about SSML markup, see:
# https://developer.amazon.com/en-US/docs/alexa/custom-skills/speech-synthesis-markup-language-ssml-reference.html
_DEFAULT_TEXT_FORMAT = "ssml"
_TEXT_FORMATS = frozenset(("text", "ssml"))

_AWS_URL_TTL = 300  # 5 mins in seconds


def _intialize_client():
    """ Set up AWS Polly client """
    global _api_client
    if _api_client:
        return _api_client

    # Read AWS Polly API keys from file
    aws_config = None
    try:
        with open(_API_KEYS_PATH) as json_file:
            aws_config = json.load(json_file)
    except FileNotFoundError:
        logging.warning("Unable to read AWS Polly keys")
        return None

    # Return client instance
    return boto3.Session(**aws_config).client("polly")


# TTL (in seconds) for get_synthesized_text_url caching
# Add a safe 30 second margin to ensure that clients are never provided with an
# audio URL that's just about to expire and could do so before playback starts.
_CACHE_TTL = _AWS_URL_TTL - 30
_CACHE_MAXITEMS = 30


@cachetools.cached(cachetools.TTLCache(_CACHE_MAXITEMS, _CACHE_TTL))
def get_synthesized_text_url(
    text, txt_format=_DEFAULT_TEXT_FORMAT, voice_id=_DEFAULT_VOICE, speed=1.0
):
    """ Returns AWS URL to audio file with speech-synthesised text """

    assert txt_format in _TEXT_FORMATS

    if voice_id not in _VOICES:
        voice_id = _DEFAULT_VOICE

    # Special preprocessing for SSML markup
    if txt_format == "ssml":
        # Adjust voice speed as appropriate
        if speed != 1.0:
            # Restrict to 50%-150% speed range
            speed = max(min(1.5, speed), 0.5)
            perc = int(speed * 100)
            text = '<prosody rate="{0}%">{1}</prosody>'.format(perc, text)
        # Wrap text in the required <speak> tag
        if not text.startswith("<speak>"):
            text = "<speak>{0}</speak>".format(text)

    client = _intialize_client()  # Set up client lazily
    if not client:
        logging.warning("Unable to instantiate AWS client")
        return None

    # Configure query string parameters for AWS request
    params = {
        # The text to synthesize
        "Text": text,
        # mp3 | ogg_vorbis | pcm
        "OutputFormat": _DEFAULT_AUDIO_FORMAT,
        # Dora or Karl
        "VoiceId": voice_id,
        # Valid values for mp3 and ogg_vorbis are "8000", "16000", and "22050".
        # The default value is "22050".
        # "SampleRate": "",
        # "text" or "ssml"
        "TextType": txt_format,
        # Only required for bilingual voices
        # "LanguageCode": "is-IS"
    }

    try:
        url = client.generate_presigned_url(
            ClientMethod="synthesize_speech",
            Params=params,
            ExpiresIn=_AWS_URL_TTL,
            HttpMethod="GET",
        )
    except ClientError as e:
        logging.error(e)
        return None

    return url


if __name__ == "__main__":
    """ Test speech synthesis through command line invocation """
    txt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Góðan daginn, félagi."

    url = get_synthesized_text_url(txt)
    print(url)
