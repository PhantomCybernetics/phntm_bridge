#!/bin/bash

# This realoads the udev rules and restarts the udev service
# which means you can reload devices created after the Docker Contained started

if which udevadm > /dev/null; then
  set +e # Disable exit on error
  udevadm control --reload-rules
  service udev restart
  udevadm trigger
  set -e # Re-enable exit on error
fi