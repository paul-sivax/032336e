export const questions = [
  "How many days?",
  "How much to invest?",
  "What matters most?",
];
export const terms = [
  {
    value: "1bd",
    label: "1 business day",
    short: "1 BD",
    group: "days",
    unit: "D",
  },
  {
    value: "2bd",
    label: "2 business days",
    short: "2 BD",
    group: "days",
    unit: "D",
  },
  {
    value: "3bd",
    label: "3 business days",
    short: "3 BD",
    group: "days",
    unit: "D",
  },
  {
    value: "5bd",
    label: "5 business days",
    short: "5 BD",
    group: "days",
    unit: "D",
  },
  {
    value: "10bd",
    label: "10 business days",
    short: "10 BD",
    group: "days",
    unit: "D",
  },
  {
    value: "1m",
    label: "1 month",
    short: "1 month",
    group: "months",
    unit: "M",
  },
  {
    value: "3m",
    label: "3 months",
    short: "3 months",
    group: "months",
    unit: "M",
  },
  {
    value: "6m",
    label: "6 months",
    short: "6 months",
    group: "months",
    unit: "M",
  },
  { value: "1y", label: "1 year", short: "1 year", group: "months", unit: "Y" },
];
export const phases: Record<string, string> = {
  queued: "Exploring the SIVAX collection",
  running: "Finding your match at SIVAX",
  completed: "Your SIVAX match is ready",
  input_required: "Your confirmation is needed",
  payment_unknown: "Payment needs reconciliation",
  failed: "This operation needs attention",
  cancelled: "Search stopped",
};
