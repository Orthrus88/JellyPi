"""Keep IPTV-org channels on Dispatcharr's single-program remux profile.

Run inside the Dispatcharr container with::

    python manage.py shell < /data/scripts/apply_iptv_stream_profile.py
"""

from apps.channels.models import Channel, StreamProfile
from apps.m3u.models import M3UAccount


ACCOUNT_NAME = "IPTV-org United States"
PROFILE_NAME = "FFmpeg Single Program"
PROFILE_PARAMETERS = (
    "-fflags +genpts+discardcorrupt -user_agent {userAgent} "
    "-i {streamUrl} -c copy "
    "-mpegts_flags +pat_pmt_at_frames+resend_headers+initial_discontinuity "
    "-f mpegts pipe:1"
)

base_profile = StreamProfile.objects.get(name="FFmpeg")
profile, _ = StreamProfile.objects.update_or_create(
    name=PROFILE_NAME,
    defaults={
        "command": "ffmpeg",
        "parameters": PROFILE_PARAMETERS,
        "locked": False,
        "is_active": True,
        "user_agent": base_profile.user_agent,
    },
)

account = M3UAccount.objects.get(name=ACCOUNT_NAME)
if account.stream_profile_id != profile.id:
    account.stream_profile = profile
    account.save(update_fields=["stream_profile"])

channels = Channel.objects.filter(streams__m3u_account=account).distinct()
updated = channels.exclude(stream_profile=profile).update(stream_profile=profile)
print(
    f"{PROFILE_NAME}: {channels.count()} IPTV-org channels checked, "
    f"{updated} updated"
)
