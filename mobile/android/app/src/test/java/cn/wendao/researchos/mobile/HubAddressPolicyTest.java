package cn.wendao.researchos.mobile;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class HubAddressPolicyTest {
    @Test
    public void privateLanAddressMayUseHttp() {
        assertEquals("http://192.168.1.20:5050", HubAddressPolicy.normalize("192.168.1.20:5050"));
        assertEquals("http://10.0.0.7:5050", HubAddressPolicy.normalize("http://10.0.0.7:5050/"));
        assertEquals("http://research-pc.local:5050", HubAddressPolicy.normalize("research-pc.local:5050"));
    }

    @Test
    public void publicAddressRequiresHttps() {
        assertThrows(
            IllegalArgumentException.class,
            () -> HubAddressPolicy.normalize("http://example.com")
        );
        assertEquals("https://hub.example.com", HubAddressPolicy.normalize("https://hub.example.com"));
    }

    @Test
    public void credentialsAndNestedPathsAreRejected() {
        assertThrows(
            IllegalArgumentException.class,
            () -> HubAddressPolicy.normalize("https://user:pass@hub.example.com")
        );
        assertThrows(
            IllegalArgumentException.class,
            () -> HubAddressPolicy.normalize("https://hub.example.com/login")
        );
    }

    @Test
    public void webViewNavigationStaysOnConfiguredOrigin() {
        assertTrue(
            HubAddressPolicy.sameOrigin(
                "https://hub.example.com/me",
                "https://hub.example.com"
            )
        );
        assertFalse(
            HubAddressPolicy.sameOrigin(
                "https://evil.example/me",
                "https://hub.example.com"
            )
        );
    }
}
