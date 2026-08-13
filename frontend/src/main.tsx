import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { SessionProvider } from "./lib/session";
import { Shell } from "./routes/Shell";
import { CampaignList } from "./routes/CampaignList";
import { Play } from "./routes/Play";
import { Workspace } from "./routes/campaign/Workspace";
import { PlayTab } from "./routes/campaign/PlayTab";
import { PartyTab } from "./routes/campaign/PartyTab";
import { InventoryTab } from "./routes/campaign/InventoryTab";
import { JournalTab } from "./routes/campaign/JournalTab";
import { CodexTab } from "./routes/campaign/CodexTab";
import "./styles/tokens.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<CampaignList />} />
            <Route path="campaigns/:campaignId" element={<Workspace />}>
              <Route index element={<PlayTab />} />
              <Route path="party" element={<PartyTab />} />
              <Route path="inventory" element={<InventoryTab />} />
              <Route path="journal" element={<JournalTab />} />
              <Route path="codex" element={<CodexTab />} />
            </Route>
            {/* Standalone, for reading a finished session. */}
            <Route path="play/:sessionId" element={<Play />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  </StrictMode>,
);
