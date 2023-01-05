"""Abode camera device."""
import base64
import logging
from shutil import copyfileobj

import requests

import jaraco
from .._itertools import single
from ..helpers import constants as CONST
from ..helpers import errors as ERROR
from ..helpers import timeline as TIMELINE
from ..helpers import urls
from . import base

_LOGGER = logging.getLogger(__name__)


class Camera(base.Device):
    """Class to represent a camera device."""

    implements = CONST.TYPE_CAMERA

    _image_url = None
    _snapshot_base64 = None

    def capture(self):
        """Request a new camera image."""
        # Abode IP cameras use a different URL for image captures.
        if 'control_url_snapshot' in self._state:
            url = self._state['control_url_snapshot']

        elif 'control_url' in self._state:
            url = self._state['control_url']

        else:
            raise jaraco.abode.Exception(ERROR.MISSING_CONTROL_URL)

        try:
            response = self._client.send_request("put", url)

            _LOGGER.debug("Capture image response: %s", response.text)

            return True

        except jaraco.abode.Exception as exc:
            _LOGGER.warning("Failed to capture image: %s", exc)

        return False

    def refresh_image(self):
        """Get the most recent camera image."""
        url = urls.TIMELINE_IMAGES_ID.format(device_id=self.device_id)
        response = self._client.send_request("get", url)

        _LOGGER.debug("Get image response: %s", response.text)

        return self.update_image_location(response.json())

    def update_image_location(self, timeline_json):
        """Update the image location."""
        if not timeline_json:
            return False

        # If timeline_json contains a list of objects (likely), use
        # the first as it should be the "newest".
        timeline = single(timeline_json)

        # Verify that the event code is of the "CAPTURE IMAGE" event
        event_code = timeline.get('event_code')
        if event_code != TIMELINE.CAPTURE_IMAGE['event_code']:
            raise jaraco.abode.Exception(ERROR.CAM_TIMELINE_EVENT_INVALID)

        # The timeline response has an entry for "file_path" that acts as the
        # location of the image within the Abode servers.
        file_path = timeline.get('file_path')
        if not file_path:
            raise jaraco.abode.Exception(ERROR.CAM_IMAGE_REFRESH_NO_FILE)

        # Perform a "head" request for the image and look for a
        # 302 Found response
        response = self._client.send_request("head", file_path)

        if response.status_code != 302:
            _LOGGER.warning(
                "Unexected response code %s with body: %s",
                str(response.status_code),
                response.text,
            )
            raise jaraco.abode.Exception(ERROR.CAM_IMAGE_UNEXPECTED_RESPONSE)

        # The response should have a location header that is the actual
        # location of the image stored on AWS
        location = response.headers.get('location')
        if not location:
            raise jaraco.abode.Exception(ERROR.CAM_IMAGE_NO_LOCATION_HEADER)

        self._image_url = location

        return True

    def image_to_file(self, path, get_image=True):
        """Write the image to a file."""
        if not self.image_url or get_image:
            if not self.refresh_image():
                return False

        response = requests.get(self.image_url, stream=True)

        if response.status_code != 200:
            _LOGGER.warning(
                "Unexpected response code %s when requesting image: %s",
                str(response.status_code),
                response.text,
            )
            raise jaraco.abode.Exception(ERROR.CAM_IMAGE_REQUEST_INVALID)

        with open(path, 'wb') as imgfile:
            copyfileobj(response.raw, imgfile)

        return True

    def snapshot(self):
        """Request the current camera snapshot as a base64-encoded string."""
        url = f"{urls.CAMERA_INTEGRATIONS}{self._device_uuid}/snapshot"

        try:
            response = self._client.send_request("post", url)
            _LOGGER.debug("Camera snapshot response: %s", response.text)
        except jaraco.abode.Exception as exc:
            _LOGGER.warning("Failed to get camera snapshot image: %s", exc)
            return False

        self._snapshot_base64 = response.json().get("base64Image")
        if self._snapshot_base64 is None:
            _LOGGER.warning("Camera snapshot data missing")
            return False

        return True

    def snapshot_to_file(self, path, get_snapshot=True):
        """Write the snapshot image to a file."""
        if not self._snapshot_base64 or get_snapshot:
            if not self.snapshot():
                return False

        try:
            with open(path, "wb") as imgfile:
                imgfile.write(base64.b64decode(self._snapshot_base64))
        except OSError as exc:
            _LOGGER.warning("Failed to write snapshot image to file: %s", exc)
            return False

        return True

    def snapshot_data_url(self, get_snapshot=True):
        """Return the snapshot image as a data url."""
        if not self._snapshot_base64 or get_snapshot:
            if not self.snapshot():
                return ""

        return f"data:image/jpeg;base64,{self._snapshot_base64}"

    def privacy_mode(self, enable):
        """Set camera privacy mode (camera on/off)."""
        if self._state['privacy']:
            privacy = '1' if enable else '0'

            path = urls.PARAMS + self.device_id

            camera_data = {
                'mac': self._state['camera_mac'],
                'privacy': privacy,
                'action': 'setParam',
                'id': self.device_id,
            }

            response = self._client.send_request(
                method="put", path=path, data=camera_data
            )
            response_object = response.json()

            _LOGGER.debug("Camera Privacy Mode Response: %s", response.text)

            if response_object['id'] != self.device_id:
                raise jaraco.abode.Exception(ERROR.SET_STATUS_DEV_ID)

            if response_object['privacy'] != str(privacy):
                raise jaraco.abode.Exception(ERROR.SET_PRIVACY_MODE)

            _LOGGER.info("Set camera %s privacy mode to: %s", self.device_id, privacy)

            return True

        return False

    @property
    def image_url(self):
        """Get image URL."""
        return self._image_url

    @property
    def is_on(self):
        """Get camera state (assumed on)."""
        return self.status not in (CONST.STATUS_OFF, CONST.STATUS_OFFLINE)
