import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { Tasks } from "./Tasks";

beforeEach(() => {
  useCharlieStore.setState({ tasks: {} });
});

describe("Tasks", () => {
  test("shows an honest empty state instead of demo tasks", () => {
    render(<Tasks />);

    expect(screen.getByText("No background tasks yet.")).toBeInTheDocument();
    expect(screen.queryByText("Threat Intelligence Scan")).not.toBeInTheDocument();
  });

  test("renders live task progress from the runtime store", () => {
    useCharlieStore.setState({
      tasks: {
        task1: { id: "task1", title: "Check deployment", status: "running", currentStep: 1, totalSteps: 2 },
      },
    });
    render(<Tasks />);

    expect(screen.getByText("Check deployment")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  test("does not render nonfunctional scheduled or view-all controls", () => {
    render(<Tasks />);

    expect(screen.queryByRole("button", { name: "Scheduled" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /View all tasks/i })).not.toBeInTheDocument();
  });
});
