// Test setup: unmount whatever a test rendered so one test's DOM never leaks
// into the next. (Testing Library only does this by itself with global hooks.)
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
