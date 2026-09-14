#!/usr/bin/env bash
# Installs the Supermicro PSU Zabbix scripts, UserParameter config, state
# directory, and the udev rule that lets the unprivileged zabbix-agent user
# access /dev/ipmi0. Run as root. Idempotent - safe to re-run on upgrade.
#
# Privilege model: rather than granting zabbix sudo rights or using a
# setuid wrapper, we create a dedicated group and a udev rule granting that
# group rw access to /dev/ipmi0, then add the zabbix user to it. This is
# the standard least-privilege pattern for BMC device access on Linux.
# See docs/TROUBLESHOOTING.md for a sudoers-based alternative if your
# environment restricts udev/group changes.

set -euo pipefail

BIN_DIR="/usr/lib/zabbix-supermicro-psu"
CONF_DIR="/etc/zabbix/zabbix_agentd.d"
STATE_DIR="/var/lib/zabbix-supermicro-psu/state"
GROUP_NAME="zbxipmi"
ZABBIX_USER="zabbix"
UDEV_RULE_PATH="/etc/udev/rules.d/99-zabbix-supermicro-psu.rules"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
    echo "install.sh must be run as root" >&2
    exit 1
fi

echo "Installing scripts to ${BIN_DIR}"
install -d -m 0755 "${BIN_DIR}"
install -m 0755 "${REPO_ROOT}/bin/psu_common.py" "${BIN_DIR}/psu_common.py"
install -m 0755 "${REPO_ROOT}/bin/psu_discover.py" "${BIN_DIR}/psu_discover.py"
install -m 0755 "${REPO_ROOT}/bin/psu_read_energy.py" "${BIN_DIR}/psu_read_energy.py"
install -m 0755 "${REPO_ROOT}/bin/psu_read_status.py" "${BIN_DIR}/psu_read_status.py"

echo "Installing UserParameter config to ${CONF_DIR}"
install -d -m 0755 "${CONF_DIR}"
install -m 0644 "${REPO_ROOT}/etc/zabbix_agentd.d/supermicro_psu.conf" \
    "${CONF_DIR}/supermicro_psu.conf"

echo "Creating state directory ${STATE_DIR}"
install -d -m 0750 "$(dirname "${STATE_DIR}")"
install -d -m 0750 -o "${ZABBIX_USER}" -g "${ZABBIX_USER}" "${STATE_DIR}"

if ! getent group "${GROUP_NAME}" >/dev/null; then
    echo "Creating group ${GROUP_NAME}"
    groupadd --system "${GROUP_NAME}"
fi

if id "${ZABBIX_USER}" >/dev/null 2>&1; then
    echo "Adding ${ZABBIX_USER} to group ${GROUP_NAME}"
    usermod -a -G "${GROUP_NAME}" "${ZABBIX_USER}"
else
    echo "Warning: user '${ZABBIX_USER}' not found - skipping group membership." >&2
    echo "Add whichever user runs zabbix-agentd to the '${GROUP_NAME}' group manually." >&2
fi

echo "Installing udev rule ${UDEV_RULE_PATH}"
install -m 0644 "${REPO_ROOT}/packaging/99-zabbix-supermicro-psu.rules" "${UDEV_RULE_PATH}"

if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules
    udevadm trigger --subsystem-match=ipmi || true
fi

echo
echo "Done. Next steps:"
echo "  1. If ${ZABBIX_USER} was just added to ${GROUP_NAME}, restart zabbix-agentd"
echo "     so the new group membership takes effect."
echo "  2. Import zabbix/templates/template_supermicro_psu.yaml into Zabbix and"
echo "     attach it to the host(s) you want to monitor."
echo "  3. Set the {\$PSU.I2C.BUSES} host macro if this chassis doesn't use bus 3."
