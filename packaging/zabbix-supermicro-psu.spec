Name:           zabbix-supermicro-psu
Version:        0.1.0
Release:        1%{?dist}
Summary:        Zabbix PSU energy monitoring for Supermicro servers via ipmitool i2c
License:        MIT
URL:            https://github.com/jannoke/zabbix-supermicro-psu
BuildArch:      noarch
Source0:        %{name}-%{version}.tar.gz
Requires:       python3, ipmitool, zabbix-agent

%description
Discovers populated Supermicro PSU bays over PMBus/I2C via the local BMC
and reports each PSU's cumulative energy usage as a Zabbix counter item,
using a stateful helper to linearize the PMBus READ_EIN accumulator's
rollover. Ships a Zabbix LLD template alongside the UserParameter scripts.

%prep
%setup -q

%build
# Nothing to build - pure Python + config files.

%install
rm -rf %{buildroot}
install -d %{buildroot}/usr/lib/zabbix-supermicro-psu
install -m 0755 bin/psu_common.py bin/psu_discover.py bin/psu_read_energy.py bin/psu_read_status.py \
    %{buildroot}/usr/lib/zabbix-supermicro-psu/
install -d %{buildroot}/etc/zabbix/zabbix_agentd.d
install -m 0644 etc/zabbix_agentd.d/supermicro_psu.conf \
    %{buildroot}/etc/zabbix/zabbix_agentd.d/supermicro_psu.conf
install -d %{buildroot}/etc/udev/rules.d
install -m 0644 packaging/99-zabbix-supermicro-psu.rules \
    %{buildroot}/etc/udev/rules.d/99-zabbix-supermicro-psu.rules
install -d -m 0750 %{buildroot}/var/lib/zabbix-supermicro-psu/state

%files
/usr/lib/zabbix-supermicro-psu/
/etc/zabbix/zabbix_agentd.d/supermicro_psu.conf
/etc/udev/rules.d/99-zabbix-supermicro-psu.rules
%dir /var/lib/zabbix-supermicro-psu
%dir /var/lib/zabbix-supermicro-psu/state
%doc README.md docs/ARCHITECTURE.md docs/TROUBLESHOOTING.md docs/SUPPORTED_HARDWARE.md
%license LICENSE

%post
udevadm control --reload-rules || true
udevadm trigger --subsystem-match=ipmi || true
getent group zbxipmi >/dev/null || groupadd --system zbxipmi
id zabbix >/dev/null 2>&1 && usermod -a -G zbxipmi zabbix || true
echo "Restart zabbix-agentd for the new device permissions to take effect."

%changelog
* Sun Sep 14 2026 zabbix-supermicro-psu maintainers - 0.1.0-1
- Initial packaging.
