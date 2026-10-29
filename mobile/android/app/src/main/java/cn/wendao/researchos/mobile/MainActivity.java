package cn.wendao.researchos.mobile;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.SafeBrowsingResponse;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public final class MainActivity extends Activity {
    private static final String PREFS = "research_os_mobile";
    private static final String HUB_URL = "hub_url";
    private static final int FILE_CHOOSER_REQUEST = 4102;

    private SharedPreferences preferences;
    private WebView webView;
    private ValueCallback<Uri[]> pendingFiles;
    private String configuredHub = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        String deepLink = hubFromIntent(getIntent());
        if (!deepLink.isEmpty()) {
            try {
                configuredHub = HubAddressPolicy.normalize(deepLink);
                showSetup("", configuredHub, true);
                return;
            } catch (IllegalArgumentException error) {
                showSetup(error.getMessage(), deepLink);
                return;
            }
        } else {
            configuredHub = preferences.getString(HUB_URL, "");
        }
        if (configuredHub.isEmpty()) {
            showSetup("", "");
        } else {
            showHub();
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String value = hubFromIntent(intent);
        if (!value.isEmpty()) {
            try {
                configuredHub = HubAddressPolicy.normalize(value);
                showSetup("", configuredHub, true);
            } catch (IllegalArgumentException error) {
                showSetup(error.getMessage(), value);
            }
        }
    }

    private String hubFromIntent(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        String scheme = data == null ? "" : data.getScheme();
        if (
            !"researchos".equalsIgnoreCase(scheme)
            && !"wendao".equalsIgnoreCase(scheme)
        ) {
            return "";
        }
        String value = data.getQueryParameter("hub");
        return value == null ? "" : value;
    }

    private void showSetup(String error, String initialValue) {
        showSetup(error, initialValue, false);
    }

    private void showSetup(String error, String initialValue, boolean autoConnect) {
        destroyWebView();
        int padding = dp(24);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(244, 237, 228));
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setGravity(Gravity.CENTER_HORIZONTAL);
        card.setPadding(padding, dp(48), padding, padding);
        scroll.addView(card, new ScrollView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        ImageView appMark = new ImageView(this);
        appMark.setImageResource(R.drawable.ic_launcher);
        appMark.setContentDescription(getString(R.string.app_name));
        card.addView(appMark, sized(dp(72), dp(72), dp(0), dp(0), dp(18)));

        TextView title = text(getString(R.string.mobile_title), 25, Color.rgb(73, 56, 46));
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        title.setGravity(Gravity.CENTER);
        card.addView(title, margins(dp(0), dp(22), dp(0), dp(8)));

        TextView description = text(
            "手机版连接你主动启动的 ResearchHub。科研论文、实验原始数据和本地数据库不会上传到手机。",
            14,
            Color.rgb(126, 103, 88)
        );
        description.setGravity(Gravity.CENTER);
        description.setLineSpacing(0, 1.35f);
        card.addView(description, margins(dp(0), dp(0), dp(0), dp(24)));

        EditText address = new EditText(this);
        address.setSingleLine(true);
        address.setText(initialValue);
        address.setHint(getString(R.string.hub_hint));
        address.setTextSize(16);
        address.setPadding(dp(14), dp(13), dp(14), dp(13));
        card.addView(address, sized(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            0,
            0,
            dp(10)
        ));

        TextView policy = text(
            "局域网可填写 192.168.x.x:5050；公网中心必须填写 https:// 地址。",
            12,
            Color.rgb(126, 103, 88)
        );
        policy.setLineSpacing(0, 1.3f);
        card.addView(policy, margins(dp(2), dp(6), dp(2), dp(14)));

        TextView errorView = text(error, 12, Color.rgb(165, 75, 66));
        errorView.setVisibility(error == null || error.isEmpty() ? View.GONE : View.VISIBLE);
        card.addView(errorView, margins(dp(2), dp(0), dp(2), dp(12)));

        Button connect = new Button(this);
        connect.setText("连接同行会");
        connect.setTextColor(Color.WHITE);
        connect.setBackgroundColor(Color.rgb(183, 103, 72));
        connect.setAllCaps(false);
        card.addView(connect, sized(
            ViewGroup.LayoutParams.MATCH_PARENT,
            dp(52),
            0,
            0,
            dp(14)
        ));
        connect.setOnClickListener(view -> {
            try {
                String candidate = HubAddressPolicy.normalize(address.getText().toString());
                verifyHubAndOpen(candidate, connect, errorView);
            } catch (IllegalArgumentException validationError) {
                errorView.setText(validationError.getMessage());
                errorView.setVisibility(View.VISIBLE);
            }
        });

        TextView steps = text(
            "1. 在电脑双击 ResearchHub.exe\n2. 记下窗口显示的 LAN 地址\n3. 手机与电脑处于同一网络后连接",
            13,
            Color.rgb(91, 111, 99)
        );
        steps.setLineSpacing(dp(5), 1.25f);
        card.addView(steps, margins(dp(4), dp(16), dp(4), dp(0)));
        setContentView(scroll);
        if (autoConnect) {
            connect.post(connect::performClick);
        }
    }

    private void verifyHubAndOpen(String candidate, Button connect, TextView errorView) {
        connect.setEnabled(false);
        connect.setText("正在验证中心…");
        errorView.setVisibility(View.GONE);
        new Thread(() -> {
            String problem = "";
            try {
                probeHub(candidate);
            } catch (Exception probeError) {
                problem = "无法确认这是可用的 ResearchHub：" + (
                    probeError.getMessage() == null ? "连接失败" : probeError.getMessage()
                );
            }
            String finalProblem = problem;
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed()) {
                    return;
                }
                connect.setEnabled(true);
                connect.setText("连接同行会");
                if (!finalProblem.isEmpty()) {
                    errorView.setText(finalProblem);
                    errorView.setVisibility(View.VISIBLE);
                    return;
                }
                configuredHub = candidate;
                preferences.edit().putString(HUB_URL, configuredHub).apply();
                showHub();
            });
        }, "research-hub-probe").start();
    }

    private void probeHub(String candidate) throws Exception {
        URL endpoint = new URL(candidate + "/.well-known/research-cultivation-os");
        HttpURLConnection connection = (HttpURLConnection) endpoint.openConnection();
        connection.setConnectTimeout(5000);
        connection.setReadTimeout(5000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("User-Agent", "ResearchOS-Mobile");
        try {
            if (connection.getResponseCode() != HttpURLConnection.HTTP_OK) {
                throw new IllegalArgumentException("中心发现接口不可用");
            }
            byte[] body;
            try (InputStream stream = connection.getInputStream();
                 ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[4096];
                int total = 0;
                int count;
                while ((count = stream.read(buffer)) != -1) {
                    total += count;
                    if (total > 65536) {
                        throw new IllegalArgumentException("中心发现信息异常");
                    }
                    output.write(buffer, 0, count);
                }
                body = output.toByteArray();
            }
            JSONObject payload = new JSONObject(new String(body, StandardCharsets.UTF_8));
            JSONObject mobile = payload.optJSONObject("mobile_client");
            if (
                !"research_hub".equals(payload.optString("role"))
                || mobile == null
                || !mobile.optBoolean("supported", false)
            ) {
                throw new IllegalArgumentException("地址不是受支持的 ResearchHub");
            }
        } finally {
            connection.disconnect();
        }
    }

    private void showHub() {
        destroyWebView();
        LinearLayout shell = new LinearLayout(this);
        shell.setOrientation(LinearLayout.VERTICAL);
        shell.setBackgroundColor(Color.rgb(255, 250, 243));

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(14), dp(8), dp(8), dp(8));
        toolbar.setBackgroundColor(Color.rgb(244, 231, 213));
        TextView title = text("科研系统 · 同行会", 15, Color.rgb(73, 56, 46));
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        toolbar.addView(title, new LinearLayout.LayoutParams(0, dp(42), 1f));
        Button home = compactButton("主页");
        Button change = compactButton("换中心");
        toolbar.addView(home);
        toolbar.addView(change);
        shell.addView(toolbar, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        ));

        webView = new WebView(this);
        configureWebView(webView);
        shell.addView(webView, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            0,
            1f
        ));
        setContentView(shell);
        home.setOnClickListener(view -> webView.loadUrl(configuredHub + "/"));
        change.setOnClickListener(view -> showSetup("", configuredHub));
        webView.loadUrl(configuredHub + "/");
    }

    private void configureWebView(WebView view) {
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(false);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(view, false);

        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView current, WebResourceRequest request) {
                Uri target = request.getUrl();
                String value = target.toString();
                if (HubAddressPolicy.sameOrigin(value, configuredHub)) {
                    return false;
                }
                String scheme = target.getScheme();
                if ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) {
                    startActivity(new Intent(Intent.ACTION_VIEW, target));
                }
                return true;
            }

            @Override
            public void onReceivedError(
                WebView current,
                WebResourceRequest request,
                WebResourceError error
            ) {
                if (request.isForMainFrame()) {
                    Toast.makeText(
                        MainActivity.this,
                        "暂时无法连接 ResearchHub，请确认电脑端仍在运行且处于同一网络。",
                        Toast.LENGTH_LONG
                    ).show();
                }
            }

            @Override
            @android.annotation.TargetApi(27)
            public void onSafeBrowsingHit(
                WebView current,
                WebResourceRequest request,
                int threatType,
                SafeBrowsingResponse callback
            ) {
                callback.backToSafety(true);
            }
        });
        view.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(
                WebView current,
                ValueCallback<Uri[]> filePathCallback,
                FileChooserParams fileChooserParams
            ) {
                if (pendingFiles != null) {
                    pendingFiles.onReceiveValue(null);
                }
                pendingFiles = filePathCallback;
                Intent chooser;
                try {
                    chooser = fileChooserParams.createIntent();
                } catch (Exception error) {
                    chooser = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                    chooser.setType("*/*");
                    chooser.addCategory(Intent.CATEGORY_OPENABLE);
                }
                startActivityForResult(chooser, FILE_CHOOSER_REQUEST);
                return true;
            }
        });
        view.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            if (HubAddressPolicy.sameOrigin(url, configuredHub)) {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            }
        });
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_CHOOSER_REQUEST || pendingFiles == null) {
            return;
        }
        Uri[] result = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
        pendingFiles.onReceiveValue(result);
        pendingFiles = null;
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        destroyWebView();
        super.onDestroy();
    }

    private void destroyWebView() {
        if (webView == null) {
            return;
        }
        webView.stopLoading();
        webView.setWebChromeClient(null);
        webView.setWebViewClient(null);
        webView.destroy();
        webView = null;
    }

    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this);
        view.setText(value == null ? "" : value);
        view.setTextSize(size);
        view.setTextColor(color);
        return view;
    }

    private Button compactButton(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(12);
        button.setAllCaps(false);
        button.setMinWidth(0);
        button.setMinimumWidth(0);
        button.setPadding(dp(10), 0, dp(10), 0);
        return button;
    }

    private LinearLayout.LayoutParams margins(int left, int top, int right, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT
        );
        params.setMargins(left, top, right, bottom);
        return params;
    }

    private LinearLayout.LayoutParams sized(int width, int height, int left, int top, int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(width, height);
        params.setMargins(left, top, left, bottom);
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
