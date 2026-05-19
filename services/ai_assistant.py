from textwrap import dedent

from phi.assistant import Assistant
from phi.llm.ollama import Ollama
from phi.llm.openai import OpenAIChat


class AIAssistant:
    def __init__(self, model: str):
        self.model = model

        # will add to system prompt
        self.description = "You are a Senior Investment Analyst for Goldman Sachs tasked with producing a research report for a very important client."

        # List of instructions added to the system prompt in `<instructions>` tags.
        self.instructions = [
            "You will be provided with a stock and information from junior researchers.",
            "Carefully read the research and generate a final - Goldman Sachs worthy investment report.",
            "Make your report engaging, informative, and well-structured.",
            "When you share numbers, make sure to include the units (e.g., millions/billions) and currency.",
            "REMEMBER: This report is for a very important client, so the quality of the report is important.",
            "Make sure your report is properly formatted and follows the <report_format> provided below.",
        ]

        # Add a string to the end of the default system prompt
        self.add_to_system_prompt = dedent(
            """
                 <report_format>
                 ## [Company Name]: Investment Report
    
                 ### **Overview**
                 {give a brief introduction of the company and why the user should read this report}
                 {make this section engaging and create a hook for the reader}
    
                 ### Core Metrics
                 {provide a summary of core metrics and show the latest data}
                 - Current price: {current price}
                 - 52-week high: {52-week high}
                 - 52-week low: {52-week low}
                 - Market Cap: {Market Cap} in billions
                 - P/E Ratio: {P/E Ratio}
                 - Earnings per Share: {EPS}
                 - 50-day average: {50-day average}
                 - 200-day average: {200-day average}
                 - Analyst Recommendations: {buy, hold, sell} (number of analysts)
    
                 ### Financial Performance
                 {provide a detailed analysis of the company's financial performance}
    
                 ### Growth Prospects
                 {analyze the company's growth prospects and future potential}
    
                 ### News and Updates
                 {summarize relevant news that can impact the stock price}
    
                 ### Upgrades and Downgrades
                 {share 2 upgrades or downgrades including the firm, and what they upgraded/downgraded to}
                 {this should be a paragraph not a table}
    
                 ### [Summary]
                 {give a summary of the report and what are the key takeaways}
    
                 ### [Recommendation]
                 {provide a recommendation on the stock along with a thorough reasoning}
    
                 Report generated on: {Month Date, Year (hh:mm AM/PM)}
                 </report_format>
            """
        )

    def _select_model(self):
        if self.model == "llama3.2":
            return Ollama(model=self.model)
        elif self.model == "gpt-4o":
            return OpenAIChat(model=self.model)
        else:
            raise ValueError(f"Model {self.model} not supported")

    def get_assistant(self):
        return Assistant(
            name="Investment Analyst",
            llm=self._select_model(),
            description=self.description,
            instructions=self.instructions,
            markdown=True,
            add_datetime_to_instructions=True,
            add_to_system_prompt=self.add_to_system_prompt,
            debug_mode=True,
        )


if __name__ == "__main__":
    assistant = AIAssistant("llama3.2").get_assistant()

    report_message = "Please generate a report about: BBRI"

    ## Streaming
    # final_report = ""
    # for delta in assistant.run(report_message):
    #     final_report += delta  # type: ignore
    #     print(final_report)

    assistant.print_response(report_message)
