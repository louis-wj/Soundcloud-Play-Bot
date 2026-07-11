# Soundcloud-Play-Bot

[![Licence: MIT](https://img.shields.io/badge/Licence-MIT-blue.svg)](LICENSE)
[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-blue.svg)](CONTRIBUTING.md)

**Soundcloud-Play-Bot** is an open-source, automated full-stack simulation tool and proxy orchestration dashboard designed to perform high-volume streaming stress tests, telemetry checking, and playback verification on SoundCloud tracks. By coordinating containerised headless browser nodes, advanced anti-fingerprinting configurations, and elite proxy rotation networks, this ecosystem bridges the gap between unreliable request scripts and industrial-grade web automation.

---

## 📖 Table of Contents
- [Features](#-features)
- [Architecture & Tech Stack](#-architecture--tech-stack)
- [Getting Started](#-getting-started)
  - [Prerequisites](#prerequisites)
  - [Local Installation](#local-installation)
  - [Environment Variables](#environment-variables)
- [Database Schema](#-database-schema)
- [API Documentation](#-api-documentation)
- [Testing](#-testing)
- [Contributing](#-contributing)
- [Licence](#-licence)

---

## 🚀 Features

Soundcloud-Play-Bot provides a comprehensive suite of features built to facilitate scalable traffic simulation and automated playback verification:

### 🌐 Advanced Proxy & Network Orchestration
* **Dynamic Proxy Rotation:** Continuous integration with HTTP/S, SOCKS4, and SOCKS5 proxy lists, handling structural connection dropouts and auto-ban evasion smoothly.
* **Geographical Targeting:** Configurable regional settings to simulate traffic coming from specific global locations for strict localized stream telemetry tests.

### 🤖 Headless Driver Automation & Humanisation
* **Stealth Fingerprinting:** Deep modification of browser fingerprints (including custom user-agent strings, canvas element overrides, and WebGL noise injection) to systematically bypass automated bot-detection firewalls.
* **Realistic User Patterns:** Programmatic simulation of mouse trajectories, variable scrolling routines, organic playback pauses, and realistic continuous audio-listening runtimes.

### 📈 Centralized Control Dashboard
* **Real-Time Stream Tracking:** High-performance interface showing live connection counts, active cluster nodes, successful tracking returns, and error rates.
* **Target Profiling Management:** A tracking repository where users can upload bulk target URLs, structure concurrent load distributions, and adjust track-skipping parameters.

### 📊 Metric Reporting & Analytics
* **Success Verification Logs:** Complete performance data storing network responses, duration targets met, and actual cloud-side validation milestones.
* **Bandwidth Overhead Mitigation:** Structural options to explicitly block redundant asset types (such as image blocks, CSS layouts, and auxiliary tracking scripts) to minimize execution network costs.

---

## 🛠️ Architecture & Tech Stack

Soundcloud-Play-Bot is engineered using a highly decoupled master-worker clustering model, facilitating horizontal scalability across distributed nodes.

              ┌────────────────────────┐
              │   Control Dashboard    │
              │   (React / Next.js)    │
              └───────────┬────────────┘
                          │ HTTPS / WSS
                          ▼
              ┌────────────────────────┐
              │  Master Orchestrator   │
              │     & Task Router      │
              └───────────┬────────────┘
                          │
     ┌────────────────────┴────────────────────┐
     ▼                                         ▼
┌─────────────────┐                       ┌─────────────────┐│ Proxy Manager & │                       │ Ephemeral Worker││ Validation Hub  │                       │ Playback Nodes  │└────────┬────────┘                       └────────┬────────┘│                                         │▼                                         ▼┌─────────────────┐                       ┌─────────────────┐│ Relational DB / │                       │ Target Platform ││ Redis Work Queue│                       │  (SoundCloud)   │└─────────────────┘                       └─────────────────┘
### Frontend
* **Core Framework:** TypeScript, React.js (or Next.js) delivering real-time statistics pipelines and script invocation controls.
* **State Management:** Zustand or Redux Toolkit coordinating concurrent task queues and proxy connection metrics natively.
* **Styling Ecosystem:** Tailwind CSS structured into a clean, scannable data-dense administration display.

### Backend
* **Runtime Environment:** Node.js with Express or NestJS managing queue dispatches and processing cluster monitoring threads.
* **Automation Engine:** Puppeteer or Playwright core wrappers running inside containerized, headless environments to perform reliable track navigation.
* **Concurrency Management:** BullMQ or custom worker threads maintaining robust background operations under high loads.

### Databases & Infrastructure
* **Persistent Storage:** PostgreSQL (or SQLite) archiving targeted URLs, success metrics, and historic run patterns safely.
* **Caching & Job Lock:** Redis handling active task distributions, monitoring proxy health status metrics, and tracking active headless nodes.

---

## 💻 Getting Started

Follow these detailed steps to stand up a local instance of Soundcloud-Play-Bot for development and verification purposes.

### Prerequisites
Ensure your local host environment runs the following baseline dependencies:
* **Node.js:** `v18.x` or later
* **Package Manager:** `npm v9.x+` or `yarn`
* **Infrastructure:** Chromium or Playwright web dependencies installed on the system host.

### Local Installation

1. **Clone the Repository:**
   ```bash
   git clone [https://github.com/louis-wj/Soundcloud-Play-Bot.git](https://github.com/louis-wj/Soundcloud-Play-Bot.git)
   cd Soundcloud-Play-Bot
Install Project Dependencies:Bashnpm install
Install Headless Browser Dependencies:Ensure the core browser binaries are safely downloaded into the local project cache:Bashnpx playwright install chromium --with-deps
# or for puppeteer instances:
npx puppeteer browsers install chrome
Prepare Proxy Lists:Create a standard newline-separated asset file named proxies.txt inside the root backend directory:Plaintext[http://username:password@192.168.1.1:8080](http://username:password@192.168.1.1:8080)
socks5://10.0.0.1:1080
Spin Up the Development Server:Bashnpm run dev
Environment VariablesYour backend .env configuration template must contain the following declarations:Code snippet# Server Network Settings
PORT=5000
NODE_ENV=development

# Database Connection URI
DATABASE_URL="postgresql://db_user:db_password@localhost:5462/sc_bot_db?schema=public"

# Concurrency & Workload Constraints
MAX_CONCURRENT_PAGES=10
MIN_PLAYBACK_DURATION_SEC=35
MAX_PLAYBACK_DURATION_SEC=90

# Caching & Queue System
REDIS_URL="redis://localhost:6379"

# Stealth Settings
HEADLESS_MODE=true
BLOCK_IMAGES_AND_CSS=true
📊 Database SchemaThe entity relationships inside Soundcloud-Play-Bot are optimized to handle massive write volumes generated by continuous tracking runs:Entity TablePrimary ResponsibilityKey Attributes IncludedTasksTracks target execution groupsid, track_url, target_plays, current_plays, status, created_atProxiesCore connection database poolid, ip_address, port, protocol, is_active, fail_countPlaybackLogsIndividual node execution logsid, task_id, proxy_used, duration_streamed, success, timestampSystemConfigsGlobal browser behavior statesid, user_agent_pool[], stealth_enabled, timeout_ms⚡ API DocumentationAll request parameters, headers, and payloads interact natively using serialization standards.Task Control EndpointsPOST /api/tasks/create - Submits a new track target payload containing configuration limits into the primary work queue.GET /api/tasks/status - Pulls comprehensive diagnostic information regarding current concurrent worker states.POST /api/tasks/:id/abort - Signals all active browser nodes linked to the target task sequence to terminate immediately.Proxy Administration EndpointsPOST /api/proxies/upload - Ingensts bulk formatted string lists directly into the active connection engine.GET /api/proxies/health - Triggers a real-time background validation run to verify the latency and status of all stored proxies.🧪 TestingSoundcloud-Play-Bot guarantees reliability through comprehensive integration test blocks using structural frameworks like Jest and Supertest.Executing System Logic TestsNavigate into your root environment to fire up unit validations and queue process tracking:Bashnpm run test
Executing Stealth Bypass VerificationsTest headless browser instances against diagnostic sites to verify the integrity of anti-fingerprinting configurations:Bashnpm run test:stealth
🤝 ContributingContributions are vital to Soundcloud-Play-Bot's continuous evolution. Please follow this structural process to introduce fixes or feature enhancements:Fork the codebase at https://github.com/louis-wj/Soundcloud-Play-Bot.Initialise a dedicated, descriptive tracking branch: git checkout -b feature/your-awesome-feature.Commit your adjustments locally ensuring message patterns align neatly with modern git practices.Push execution states up to your repository copy: git push origin feature/your-awesome-feature.Open a detailed Pull Request outlining your architectural changes, visual improvements, or performance patches.📄 LicenceDistributed strictly under the terms of the MIT Licence. Review the complete layout parameters inside the local LICENSE asset file for comprehensive legal parameters.
