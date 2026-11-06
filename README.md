<!-- =========================================================
  ISS Mimic  •  Re-create the ISS attitude & telemetry in real-time
  ========================================================= -->

<p align="center">
  <img src="Pi/imgs/main/ISSmimicLogoPartsGroundtrack.png" width="420" alt="ISS Mimic logo">
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
  <img alt="Last commit" src="https://img.shields.io/github/last-commit/SINtyanneru/ISS-Mimic">
  <a href="https://discord.gg/zPKyE6hBSe"><img alt="Discord Chat" src="https://img.shields.io/discord/764217406041882684"></a>
  <a href="https://www.youtube.com/watch?v=W9iZBjzOEEQ"><img alt="Demo" src="https://img.shields.io/badge/▶ Demo%20Video-red?logo=youtube&logoColor=white"></a>
</p>

> **ISS Mimic** は、国際宇宙ステーション（ISS）の太陽電池パドルとラジエーターの動きをリアルタイムに再現する、ハードウェア＋ソフトウェアのオープンソースプロジェクトです。
> Raspberry Pi で動作 • 12個のモーターで3Dプリント製ISSを駆動 • ライブテレメトリ表示

---

## このリポジトリについて（フォーク）

このリポジトリは、本家 **[ISS-Mimic/Mimic](https://github.com/ISS-Mimic/Mimic)** のフォークです。

ISS Mimic という素晴らしいプロジェクトを生み出し、長年育ててきた本家の開発チームとコミュニティに、心から敬意と感謝を表します。このフォークの内容のほぼすべては本家の成果物であり、本家なくして存在しません。プロジェクトの最新情報・正規のドキュメント・コミュニティへの参加は、ぜひ本家をご覧ください。

- 本家リポジトリ: https://github.com/ISS-Mimic/Mimic
- 本家 Wiki: https://github.com/ISS-Mimic/Mimic/wiki
- 本家 Discord: [Mimic Discord](https://discord.gg/zPKyE6hBSe)

### このフォークでの変更点

本家への変更は最小限に絞り、以下の2点だけを加えています。

1. **README の日本語化**（このファイル）
2. **テレメトリの MySQL 保存対応**（`Pi/iss_telemetry.py`）
   - 既定では従来どおりローカル SQLite に保存します
   - `Pi/config.ini` の `[mysql]` セクションで `enabled = true` にすると、受信したテレメトリを MySQL の `telemetry` テーブルに保存します（テーブルは初回起動時に自動作成）

---

ISS Mimic は、実際のISSのライブデータに接続し、太陽電池パドルやラジエーターを本物と同期して回転させる、3Dプリント製の国際宇宙ステーション模型です。目標は、人々とISSをつなぐこと。モデルには Mimic、Mini Mimic、Edu Mimic の3種類があり、教師・学生・博物館・ホビイストなど、ISSについて学びたいすべての人のために設計されています。

議論への参加、協力の申し出、助けを求める場、ISSについての雑談はこちら: [Mimic Discord](https://discord.gg/zPKyE6hBSe)（本家のDiscordです）

![PXL_20231210_003701830](https://github.com/user-attachments/assets/27b69560-8007-48d9-a087-6f27fd00f06d)

<details>
<summary>目次</summary>

- [特徴](#特徴)
- [クイックスタート（Raspberry Pi）](#クイックスタートraspberry-pi)
- [テレメトリについて](#テレメトリについて)
- [プロジェクト構成](#プロジェクト構成)
- [自分でモデルを作る](#自分でモデルを作る)
- [スクリーンショット](#スクリーンショット)
- [コントリビュート](#コントリビュート)
- [ロードマップ](#ロードマップ)
- [謝辞](#謝辞)
- [ライセンス](#ライセンス)
</details>

---

### 特徴
- **ライブテレメトリ取得**: NASA の Lightstreamer エンドポイントから取得。スクレイピング不要
- **インタラクティブなダッシュボード**（当時の ISSLive サイトを超える機能）
- **リアルタイムなキネマティクス**: 12軸の動きが α・β ジンバルの関節角に一致
- **モジュール設計**: Pi（テレメトリ＆UI）↔ Arduino（モーター制御）を分離
- **低／高精細の STL** を今すぐ印刷可能

### クイックスタート（Raspberry Pi）
```bash
# 1. クローン＆依存関係のインストール
  git clone --depth 1 https://github.com/SINtyanneru/ISS-Mimic.git/
  cd ~/ISS-Mimic
  python setup.py

# 2. Mimic GUI ダッシュボードを起動
  cd ~/ISS-Mimic/Pi/
  python GUI.py
```
ソフトウェアの完全なガイド: [Wiki » Software Setup Instructions](https://github.com/ISS-Mimic/Mimic/wiki/Build-Instruction%3A-Mimic-Software-Setup-Instructions)（本家Wiki、英語）

### テレメトリについて
テレメトリとは、ISS の各システムの運用状態を監視するために、ISS から収集され、ISS管制センターへ送信されるデータのことです。

2011年、JSC（ジョンソン宇宙センター）の素晴らしいスタッフの方々が、10万項目以上あるISSテレメトリの一部を、ISSlive! プロジェクトとそのウェブサイトを通じて一般公開しました。
残念ながらそのウェブサイトはすでに終了していますが、彼らが開いた扉のおかげで、その後に続く何十ものプロジェクトが生まれました。

NASA は [Lightstreamer](https://lightstreamer.com/) 社と契約し、このISSテレメトリの一部を一般へ配信しています。これが、今日のすべての公開テレメトリプロジェクトの源になっています。

本家では、公開データのすべて（と Pi アプリの画面）を紹介するテレメトリページを3つ用意しています。

フィルタ可能なテーブル: https://iss-mimic.github.io/Mimic/ （ロシア語版: https://iss-mimic.github.io/Mimic/index_ru.html ）
優れたダッシュボード: https://iss-mimic.github.io/Mimic/dashboard.html

### プロジェクト構成
```text
┌──────────┐              ┌──────────────┐                ┌──────────────┐                 ┌──────────┐
│ NASA LS  │────────────▶│ Raspberry Pi │───────────────▶│  Arduino(s)  │───────────────▶│  Motors  │
└──────────┘  telemetry   └──────────────┘  joint angles  └──────────────┘  motor commands └──────────┘
```
*Pi 側*（Python + Kivy）はテレメトリダッシュボードを表示し、関節の目標角を送ります。
*Arduino 側*（C++）は、3Dプリント製トラス内のステッピング／サーボモーターを駆動します。

### 自分でモデルを作る
| 精細度 | STL パック | 状態 |
|----------|----------|--------|
| Low      | [`/3D_Printing`](3D_Printing) | ✔ 完成 |
| High     | `/3D_Printing/high_fidelity` | 進行中 – コントリビューター歓迎！ |

機構の詳細、BOM、配線は **[ハードウェア Wiki セクション](https://github.com/ISS-Mimic/Mimic/wiki/Hardware)**（本家）にあります。

### スクリーンショット
<p align="center">
  <img src="https://github.com/user-attachments/assets/071d90c0-504c-4c9c-95a2-5e7aa4a1db94" width="250" alt="Mimic1">
  <img src="https://github.com/user-attachments/assets/2579f6cb-1be1-4827-b076-0dc5c3374468" width="250" alt="Mimic2">
  <img src="https://github.com/user-attachments/assets/b616095f-93a2-427e-b801-ac2992f1a6f7" width="250" alt="Mimic3">
  <br>
  <img src="https://github.com/user-attachments/assets/737c447c-e9c7-4a40-9878-38ae68ba5e51" width="250" alt="Mimic4">
  <img src="https://github.com/user-attachments/assets/92358d93-7acd-4216-acdd-765e4efc8fc7" width="250" alt="Mimic5">
  <img src="https://github.com/user-attachments/assets/3afdb129-0f15-4075-b9af-4b07f93a4e77" width="250" alt="Mimic6">
  <br>
  <img src="https://github.com/user-attachments/assets/25cdaaa9-a04e-401b-a5d2-0039b911d47c" width="250" alt="Mimic7">
  <img src="https://github.com/user-attachments/assets/c21a9d0b-10a5-4251-90ff-319e66b724d0" width="250" alt="Mimic8">
  <img src="https://github.com/user-attachments/assets/5c7bd170-9f16-42e7-be29-4d884cc17b1a" width="250" alt="Mimic9">
</p>

### コントリビュート
改善案やバグの提案は、本家の Issue を立てるか **[Discord](https://discord.gg/zPKyE6hBSe)** で声をかけてください。
コーディングガイドライン:
1. **PEP 8** に従い、GUI レイアウトは **`.kv` ファイル** に置くこと（Kivy のベストプラクティス）。
2. 私たちのミスは全部直してください。

その他の協力も歓迎です:
- アイデアの提案は自由にどうぞ！ 話すのに最適な場所は本家の Discord です。Issue を公開しても構いません

### ロードマップ
- [ ] 高精細 CAD を完成させ、STEP ソースを公開
- [ ] ビルドガイドを完成
- [ ] Kivy テレメトリ画面を完成

### 謝辞
このプロジェクトを前進させてきたすべての方々へ。詳しくは [`Credits.txt`](Credits.txt) をご覧ください。とりわけ、このデータを最初に一般公開した JSC の ISSlive チームの皆さま、ISSlive を復旧させた JSC の方々、そして本家 ISS Mimic の開発チームに感謝します。

### ライセンス
このプロジェクトは MIT License のもとで公開されています。詳細は [`LICENSE`](LICENSE) をご覧ください。本家 ISS Mimic も同じ MIT License です。

<details>
<summary>コードは酷くてひどいけど、だいたい動きます（愚痴はこちらをクリック）</summary>

私たちは本業ハードウェアエンジニアで、片手間にコードを書いています。カフェイン駆動のハックや、時折のリファクタリング騒動にご期待ください。コントリビューションと建設的なフィードバックは大歓迎です！
</details>
