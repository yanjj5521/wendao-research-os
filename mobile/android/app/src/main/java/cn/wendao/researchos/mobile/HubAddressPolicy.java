package cn.wendao.researchos.mobile;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

final class HubAddressPolicy {
    private HubAddressPolicy() {}

    static String normalize(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) {
            throw new IllegalArgumentException("请输入联机中心地址。");
        }
        if (!value.contains("://")) {
            value = "http://" + value;
        }
        final URI uri;
        try {
            uri = new URI(value);
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException("中心地址格式无效。");
        }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        String host = uri.getHost() == null ? "" : uri.getHost().toLowerCase(Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https")) {
            throw new IllegalArgumentException("中心地址只能使用 http:// 或 https://。");
        }
        if (host.isEmpty() || uri.getUserInfo() != null) {
            throw new IllegalArgumentException("中心地址缺少有效主机，且不能包含用户名或密码。");
        }
        if (uri.getQuery() != null || uri.getFragment() != null) {
            throw new IllegalArgumentException("中心地址不要包含查询参数或片段。");
        }
        String path = uri.getPath() == null ? "" : uri.getPath();
        if (!path.isEmpty() && !path.equals("/")) {
            throw new IllegalArgumentException("请输入 ResearchHub 根地址，不要附加页面路径。");
        }
        if (scheme.equals("http") && !isPrivateHost(host)) {
            throw new IllegalArgumentException("公网中心必须使用 HTTPS；HTTP 只允许本机或私有局域网地址。");
        }
        int port = uri.getPort();
        if (port < -1 || port == 0) {
            throw new IllegalArgumentException("中心端口无效。");
        }
        String authority = host.contains(":") ? "[" + host + "]" : host;
        if (port > 0) {
            authority += ":" + port;
        }
        return scheme + "://" + authority;
    }

    static boolean sameOrigin(String candidate, String configuredBase) {
        try {
            URI left = new URI(candidate);
            URI right = new URI(configuredBase);
            return equalsIgnoreCase(left.getScheme(), right.getScheme())
                && equalsIgnoreCase(left.getHost(), right.getHost())
                && effectivePort(left) == effectivePort(right);
        } catch (URISyntaxException error) {
            return false;
        }
    }

    private static int effectivePort(URI uri) {
        if (uri.getPort() > 0) {
            return uri.getPort();
        }
        return "https".equalsIgnoreCase(uri.getScheme()) ? 443 : 80;
    }

    private static boolean equalsIgnoreCase(String left, String right) {
        return left != null && right != null && left.equalsIgnoreCase(right);
    }

    private static boolean isPrivateHost(String host) {
        if (host.equals("localhost") || host.endsWith(".local") || host.equals("::1")) {
            return true;
        }
        String[] parts = host.split("\\.");
        if (parts.length != 4) {
            return false;
        }
        int[] octets = new int[4];
        try {
            for (int index = 0; index < 4; index++) {
                if (parts[index].isEmpty()) {
                    return false;
                }
                octets[index] = Integer.parseInt(parts[index]);
                if (octets[index] < 0 || octets[index] > 255) {
                    return false;
                }
            }
        } catch (NumberFormatException error) {
            return false;
        }
        return octets[0] == 10
            || octets[0] == 127
            || (octets[0] == 169 && octets[1] == 254)
            || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31)
            || (octets[0] == 192 && octets[1] == 168);
    }
}
