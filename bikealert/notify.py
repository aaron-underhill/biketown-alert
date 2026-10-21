"""
Push notifications through ntfy (https://ntfy.sh).

ntfy is a free service: install the app, subscribe to a topic name, and any
message posted to that topic shows up on your phone. Anyone who knows the
topic name can read it, so use a long random one and keep it secret.
"""

import urllib.request


def send(
    server: str,
    topic: str,
    message: str,
    title: str,
    priority: str = "default",
    tags: str = "",
    click_url: str = "",
) -> None:
    headers = {
        "Title": title,
        "Priority": priority,
    }

    if tags:
        headers["Tags"] = tags

    if click_url:
        headers["Click"] = click_url

    request = urllib.request.Request(
        f"{server}/{topic}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20):
        pass


def walking_directions_url(lat: float, lon: float) -> str:
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={lat:.6f},{lon:.6f}&travelmode=walking"
    )
