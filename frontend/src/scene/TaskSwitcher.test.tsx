import { describe, expect, test, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { TaskSwitcher } from "./TaskSwitcher";
import { useCharlieStore } from "../store/charlie";

describe("TaskSwitcher Component", () => {
  beforeEach(() => {
    useCharlieStore.setState({ tasks: {} });
  });

  test("hidden when 0 or 1 task exists", () => {
    const { container } = render(<TaskSwitcher />);
    expect(container.firstChild).toBeNull();
  });

  test("renders switcher toolbar when multiple tasks exist (> 1)", () => {
    useCharlieStore.setState({
      tasks: {
        t1: {
          id: "t1",
          title: "Surveillance Scan",
          status: "running",
          currentStep: 2,
          totalSteps: 4,
          progress: 0.5,
        },
        t2: {
          id: "t2",
          title: "Data Ingestion",
          status: "running",
          currentStep: 1,
          totalSteps: 3,
          progress: 0.33,
        },
      },
    });

    render(<TaskSwitcher />);
    expect(screen.getByText("TASKS [2]")).toBeDefined();
    expect(screen.getByText("Surveillance Scan")).toBeDefined();
    expect(screen.getByText("Data Ingestion")).toBeDefined();
  });
});
