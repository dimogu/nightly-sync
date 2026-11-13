package com.fongmi.android.tv.utils;

import org.json.JSONArray;
import org.json.JSONObject;

public class Github {
    // 更新源：指向你自己 fork 后的发布仓库（即本仓库自身，GitHub Releases 会发布到这里）。
    // ★ 这一行在 GitHub Actions 构建时会被自动替换成 ${{ github.repository }}（= 本仓库路径），
    //   所以你【无需手动修改】。App 只会去检查【你自己发布的 Release】，下载的 APK 同样内置自动更新，形成自更新闭环。
    // 注意：必须是 "owner/repo" 形式，不要带 https:// 等前缀。
    private static final String REPO = "owner/repo";

    public static String getReleases() {
        return "https://api.github.com/repos/" + REPO + "/releases?per_page=100";
    }

    public static JSONObject getUpdate(JSONArray releases, String currentVersion, String filename) {
        int[] latest = version(currentVersion);
        if (latest == null) throw new IllegalArgumentException("Invalid app version");
        JSONObject selected = null;
        for (int i = 0; i < releases.length(); i++) {
            JSONObject release = releases.optJSONObject(i);
            if (release == null || release.optBoolean("draft") || release.optBoolean("prerelease") || getAsset(release, filename) == null) continue;
            int[] candidate = version(release.optString("tag_name"));
            if (candidate == null) continue;
            if (isNewer(candidate, latest)) {
                latest = candidate;
                selected = release;
            }
        }
        return selected;
    }

    private static boolean isNewer(int[] candidate, int[] current) {
        for (int i = 0; i < current.length; i++) if (candidate[i] != current[i]) return candidate[i] > current[i];
        return false;
    }

    private static int[] version(String tag) {
        String[] parts = (tag.startsWith("v") ? tag.substring(1) : tag).split("\\.", -1);
        if (parts.length != 3) return null;
        int[] version = new int[3];
        try {
            for (int i = 0; i < parts.length; i++) {
                if (!parts[i].matches("[0-9]+")) return null;
                version[i] = Integer.parseInt(parts[i]);
            }
        } catch (NumberFormatException e) {
            return null;
        }
        return version;
    }

    public static String getNotes(String body, String flavor) {
        String heading = "leanback".equals(flavor) ? "# 電視版 " : "# 手機版 ";
        StringBuilder notes = new StringBuilder();
        boolean hasSections = false;
        boolean selected = false;
        for (String line : body.split("\\r?\\n")) {
            String text = line.trim();
            if (text.startsWith("# 電視版 ") || text.startsWith("# 手機版 ")) {
                hasSections = true;
                selected = text.startsWith(heading);
            } else if (text.startsWith("# ")) {
                selected = false;
            } else if (selected && !text.isEmpty()) {
                appendNote(notes, text);
            }
        }
        return hasSections ? notes.toString() : body.trim();
    }

    private static void appendNote(StringBuilder notes, String text) {
        if (notes.length() > 0) notes.append('\n');
        if (text.startsWith("* ") || text.startsWith("- ")) text = "• " + text.substring(2);
        notes.append(text);
    }

    public static JSONObject getAsset(JSONObject release, String name) {
        JSONArray assets = release.optJSONArray("assets");
        if (assets == null) return null;
        for (int i = 0; i < assets.length(); i++) {
            JSONObject asset = assets.optJSONObject(i);
            if (asset != null && name.equals(asset.optString("name")) && "uploaded".equals(asset.optString("state"))) return asset;
        }
        return null;
    }
}
