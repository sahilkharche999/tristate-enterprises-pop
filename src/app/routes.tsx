import { createBrowserRouter } from "react-router";
import { LoginScreen } from "./components/LoginScreen";
import { HOAWorkspace } from "./components/HOAWorkspace";
import { BudgetScreen } from "./components/BudgetScreen";
import { SettingsScreen } from "./components/SettingsScreen";
import { SettingsSelector } from "./components/SettingsSelector";
import { BudgetScreenWrapper } from "./components/BudgetScreenWrapper";
import { SyncHistoryScreen } from "./components/SyncHistoryScreen";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: LoginScreen,
  },
  {
    path: "/workspace",
    Component: HOAWorkspace,
  },
  {
    path: "/settings",
    Component: SettingsSelector,
  },
  {
    path: "/hoa/:id",
    Component: BudgetScreenWrapper,
  },
  {
    path: "/hoa/:id/settings",
    Component: SettingsScreen,
  },
  {
    path: "/hoa/:id/sync-history",
    Component: SyncHistoryScreen,
  },
]);