import { createBrowserRouter } from "react-router";
import { LoginScreen } from "./components/LoginScreen";
import { SignupScreen } from "./components/SignupScreen";
import { HOAWorkspace } from "./components/HOAWorkspace";
import { SettingsScreen } from "./components/SettingsScreen";
import { SettingsSelector } from "./components/SettingsSelector";
import { BudgetScreenWrapper } from "./components/BudgetScreenWrapper";
import { SyncHistoryScreen } from "./components/SyncHistoryScreen";
import { DisclosureWorkspaceScreen } from "./components/DisclosureWorkspaceScreen";
import { AssessmentMappingReviewScreen } from "./components/AssessmentMappingReviewScreen";
import { ProtectedRoute } from "./components/ProtectedRoute";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: LoginScreen,
  },
  {
    path: "/signup",
    Component: SignupScreen,
  },
  {
    path: "/workspace",
    element: <ProtectedRoute><HOAWorkspace /></ProtectedRoute>,
  },
  {
    path: "/settings",
    element: <ProtectedRoute><SettingsSelector /></ProtectedRoute>,
  },
  {
    path: "/hoa/:id",
    element: <ProtectedRoute><BudgetScreenWrapper /></ProtectedRoute>,
  },
  {
    path: "/hoa/:id/settings",
    element: <ProtectedRoute><SettingsScreen /></ProtectedRoute>,
  },
  {
    path: "/hoa/:id/disclosure",
    element: <ProtectedRoute><DisclosureWorkspaceScreen /></ProtectedRoute>,
  },
  {
    path: "/hoa/:id/assessment-mapping-review",
    element: <ProtectedRoute><AssessmentMappingReviewScreen /></ProtectedRoute>,
  },
  {
    path: "/hoa/:id/sync-history",
    element: <ProtectedRoute><SyncHistoryScreen /></ProtectedRoute>,
  },
]);
