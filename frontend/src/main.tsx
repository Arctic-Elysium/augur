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
import { Placeholder } from "./routes/campaign/Placeholder";
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
              <Route
                path="inventory"
                element={
                  <Placeholder
                    title="Inventory"
                    body="What the party carries, what they have stashed, and who is holding it. Next up."
                  />
                }
              />
              <Route
                path="journal"
                element={
                  <Placeholder
                    title="Journal"
                    body="Your own notes, kept separate from what the game master wrote. Coming after inventory."
                  />
                }
              />
              <Route
                path="codex"
                element={
                  <Placeholder
                    title="Codex"
                    body="People met, places seen, and what the party has established as true. Waiting on the memory layer."
                  />
                }
              />
            </Route>
            {/* Standalone, for reading a finished session. */}
            <Route path="play/:sessionId" element={<Play />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  </StrictMode>,
);
