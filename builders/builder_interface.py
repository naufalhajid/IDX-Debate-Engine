from abc import abstractmethod, ABC


class BuilderInterface(ABC):
    @abstractmethod
    def insert_stock(self):
        raise NotImplementedError

    @abstractmethod
    def insert_key_statistic(self):
        raise NotImplementedError

    @abstractmethod
    def insert_key_analysis(self):
        raise NotImplementedError

    @abstractmethod
    def insert_sentiment(self):
        raise NotImplementedError

    @abstractmethod
    def insert_stock_price(self):
        raise NotImplementedError
