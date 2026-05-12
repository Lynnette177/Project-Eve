from ai.chat import evedb

if __name__ == "__main__":
    result = evedb.get_recent_messages()
    print(result)
    print("\n\n\n")
    print(evedb.get_latest_completed_turn_messages())
    