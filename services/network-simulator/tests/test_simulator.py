import pytest


def get(client, customer, path):
    return client.get(f"/customers/{customer}/{path}")


def post(client, customer, path, **json):
    return client.post(f"/customers/{customer}/{path}", json=json or None)


def test_all_scenario_customers_are_loaded(client):
    ids = [c["customer_id"] for c in client.get("/customers").json()]
    assert ids == ["C1001", "C1002", "C1003", "C1004", "C1005", "C1006"]


def test_installed_base_describes_the_connection(client):
    body = get(client, "C1004", "installed-base").json()
    assert body["carrier_type"] == "copper"
    assert body["carrier_tech_type"] == "VDSL"
    assert body["equipment"]["ont"] is None


def test_led_values_stay_strings(client):
    # YAML would turn an unquoted off into False; guard against that regression.
    for cid in ("C1001", "C1002", "C1003", "C1004", "C1005", "C1006"):
        leds = get(client, cid, "led-status").json()
        for group in leds.values():
            for value in (group or {}).values():
                assert isinstance(value, str), (cid, value)


def test_los_red_customer_has_no_optical_signal(client):
    ont = get(client, "C1001", "ont-status").json()
    assert ont["optical_signal"] is False
    assert ont["leds"]["los"] == "red"
    assert get(client, "C1001", "ppp-session").json()["status"] == "down"


def test_reboot_does_not_fix_a_los_customer(client):
    assert post(client, "C1001", "reboot-device").json()["accepted"] is True
    assert get(client, "C1001", "ppp-session").json()["status"] == "down"
    assert get(client, "C1001", "device-details").json()["modem"]["reboot_count"] == 1


def test_reboot_fixes_a_hung_modem_and_records_history(client):
    assert get(client, "C1002", "ppp-session").json()["status"] == "down"
    post(client, "C1002", "reboot-device", target="modem")
    assert get(client, "C1002", "ppp-session").json()["status"] == "up"
    history = get(client, "C1002", "ppp-history").json()
    assert history[0]["status"] == "up" and history[0]["reason"] == "after reboot_device"
    assert get(client, "C1002", "led-status").json()["modem"]["internet"] == "green"


def test_wifi_reset_restores_sticker_values_and_reconnects_devices(client):
    before = get(client, "C1003", "wifi-settings").json()
    assert before["matches_factory_defaults"] is False and before["hidden"] is True
    assert not any(d["connected"] for d in get(client, "C1003", "managed-devices").json())

    post(client, "C1003", "reset-wifi")

    after = get(client, "C1003", "wifi-settings").json()
    assert after["matches_factory_defaults"] is True and after["ssid"] == "NetCo-1003"
    assert "password" not in after
    assert all(d["connected"] for d in get(client, "C1003", "managed-devices").json())


def test_faulty_modem_survives_reboot_and_factory_reset(client):
    post(client, "C1004", "reboot-device")
    post(client, "C1004", "factory-reset-device")
    assert get(client, "C1004", "ppp-session").json()["status"] == "down"
    assert get(client, "C1004", "access-port").json()["status"] == "up"


def test_p2p_customer_has_access_port_down(client):
    port = get(client, "C1005", "access-port").json()
    assert port["kind"] == "p2p_switch" and port["status"] == "down"


def test_healthy_customer_is_fully_up(client):
    assert get(client, "C1006", "ppp-session").json()["status"] == "up"
    assert get(client, "C1006", "ont-status").json()["optical_signal"] is True


def test_network_map_shape_follows_technology(client):
    fiber = get(client, "C1002", "network-map").json()
    assert "ont" in fiber and "access_port" not in fiber
    copper = get(client, "C1004", "network-map").json()
    assert "access_port" in copper and "ont" not in copper


@pytest.mark.parametrize("customer,path", [("C1004", "ont-status"), ("C1002", "access-port")])
def test_inapplicable_data_returns_409(client, customer, path):
    response = get(client, customer, path)
    assert response.status_code == 409
    assert "detail" in response.json()


def test_rebooting_a_missing_ont_returns_409(client):
    assert post(client, "C1004", "reboot-device", target="ont").status_code == 409


def test_unknown_customer_returns_404(client):
    assert get(client, "C9999", "ppp-session").status_code == 404
    assert client.post("/admin/reset", params={"customer_id": "C9999"}).status_code == 404


def test_state_is_isolated_per_customer_and_reset_restores_it(client):
    post(client, "C1002", "reboot-device")
    assert get(client, "C1006", "device-details").json()["modem"]["reboot_count"] == 0
    assert client.post("/admin/reset", params={"customer_id": "C1002"}).status_code == 200
    assert get(client, "C1002", "ppp-session").json()["status"] == "down"
    assert get(client, "C1002", "device-details").json()["modem"]["reboot_count"] == 0
