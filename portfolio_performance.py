import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from requests.exceptions import ConnectionError
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX
import warnings
from tenacity import retry, stop_after_attempt, wait_random_exponential
from pypfopt import expected_returns, risk_models, objective_functions
warnings.filterwarnings('ignore')
from pymongo import MongoClient
import time


# Função para carregar os ativos do CSV
@st.cache_data(ttl=3600)
def load_assets():
    return pd.read_csv('https://raw.githubusercontent.com/richardrt13/Data-Science-Portifolio/main/ativos.csv')

# Função para obter dados fundamentais de um ativo
@st.cache_data(ttl=3600)
def get_fundamental_data(ticker, max_retries=3):
    for attempt in range(max_retries):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            # Obter dados do balanço patrimonial e demonstração financeira
            balance_sheet = stock.balance_sheet
            financials = stock.financials

            # Calcular o ROIC
            if not balance_sheet.empty and not financials.empty:
                net_income = financials.loc['Net Income'].iloc[0]  # Último ano fiscal
                total_assets = balance_sheet.loc['Total Assets'].iloc[0]  # Último ano fiscal
                total_liabilities = balance_sheet.loc['Total Liabilities Net Minority Interest'].iloc[0]  # Último ano fiscal
                cash = balance_sheet.loc['Cash And Cash Equivalents'].iloc[0]  # Último ano fiscal

                invested_capital = total_assets - total_liabilities - cash
                if invested_capital != 0:
                    roic = (net_income / invested_capital) * 100  # em percentagem
                else:
                    roic = np.nan
            else:
                roic = np.nan

            return {
                'P/L': info.get('trailingPE', np.nan),
                'P/VP': info.get('priceToBook', np.nan),
                'ROE': info.get('returnOnEquity', np.nan),
                'Volume': info.get('averageVolume', np.nan),
                'Price': info.get('currentPrice', np.nan),
                'ROIC': roic,
                'Dividend Yield': info.get('trailingAnnualDividendYield', np.nan),
                'Debt to Equity': info.get('debtToEquity', np.nan)
            }
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                st.warning(f"Não foi possível obter dados para {ticker}. Erro: {e}")
                return {
                    'P/L': np.nan,
                    'P/VP': np.nan,
                    'ROE': np.nan,
                    'Volume': np.nan,
                    'Price': np.nan,
                    'ROIC': np.nan,
                    'Dividend Yield': np.nan,
                    'Debt to Equity': np.nan
                }

# Função para obter dados históricos de preços com tratamento de erro
@st.cache_data(ttl=3600)
def get_stock_data(tickers, years=5, max_retries=3):
    end_date = datetime.now()
    start_date = end_date - timedelta(days=years*365)
    

    for attempt in range(max_retries):
        try:
            data = yf.download(tickers, start=start_date, end=end_date)['Adj Close']
            return data
        except ConnectionError as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                st.error(f"Erro ao obter dados históricos. Possível limite de requisição atingido. Erro: {e}")
                return pd.DataFrame()

# Função para calcular o retorno acumulado
@st.cache_data(ttl=3600)
def get_cumulative_return(ticker):
    stock = yf.Ticker(ticker)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=5*365)
    hist = stock.history(start=start_date, end=end_date)
    if len(hist) > 0:
        cumulative_return = (hist['Close'].iloc[-1] / hist['Close'].iloc[0]) - 1
    else:
        cumulative_return = None
    return cumulative_return

def calculate_returns(prices):
    if prices.empty:
        return pd.DataFrame()
    returns = prices.pct_change().dropna()
    # Remove infinitos e NaNs
    returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
    return returns

# Função para calcular o desempenho do portfólio
def portfolio_performance(weights, returns):
    portfolio_return = np.sum(returns.mean() * weights) * 252
    portfolio_volatility = np.sqrt(np.dot(weights.T, np.dot(returns.cov() * 252, weights)))
    return portfolio_return, portfolio_volatility


# Função para calcular o índice de Sharpe negativo (para otimização)
def negative_sharpe_ratio(weights, returns, risk_free_rate):
    p_return, p_volatility = portfolio_performance(weights, returns)
    return -(p_return - risk_free_rate) / p_volatility

# Função para otimizar o portfólio
def optimize_portfolio(returns, risk_free_rate):
    num_assets = returns.shape[1]
    args = (returns, risk_free_rate)
    constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
    bound = (0.0, 1.0)
    bounds = tuple(bound for asset in range(num_assets))
    result = minimize(negative_sharpe_ratio, num_assets*[1./num_assets], args=args,
                      method='SLSQP', bounds=bounds, constraints=constraints)
    return result.x

# Função para gerar portfólios aleatórios
def generate_random_portfolios(returns, num_portfolios=5000):
    results = []
    n_assets = returns.shape[1]
    for _ in range(num_portfolios):
        weights = np.random.random(n_assets)
        weights /= np.sum(weights)
        p_return, p_volatility = portfolio_performance(weights, returns)
        results.append({
            'Return': p_return,
            'Volatility': p_volatility,
            'Sharpe': (p_return - risk_free_rate) / p_volatility,
            'Weights': weights
        })
    return pd.DataFrame(results)

# Função para plotar a fronteira eficiente
def plot_efficient_frontier(returns, optimal_portfolio):
    portfolios = generate_random_portfolios(returns)

    fig = go.Figure()

    # Plotar portfólios aleatórios
    fig.add_trace(go.Scatter(
        x=portfolios['Volatility'],
        y=portfolios['Return'],
        mode='markers',
        marker=dict(
            size=5,
            color=portfolios['Sharpe'],
            colorscale='Viridis',
            colorbar=dict(title='Índice de Sharpe'),
            showscale=True
        ),
        text=portfolios['Sharpe'].apply(lambda x: f'Sharpe: {x:.3f}'),
        hoverinfo='text+x+y',
        name='Portfólios'
    ))

    # Plotar portfólio ótimo
    opt_return, opt_volatility = portfolio_performance(optimal_portfolio, returns)
    opt_sharpe = (opt_return - risk_free_rate) / opt_volatility

    fig.add_trace(go.Scatter(
        x=[opt_volatility],
        y=[opt_return],
        mode='markers',
        marker=dict(
            size=15,
            color='red',
            symbol='star'
        ),
        text=[f'Portfólio Ótimo<br>Sharpe: {opt_sharpe:.3f}'],
        hoverinfo='text+x+y',
        name='Portfólio Ótimo'
    ))

    fig.update_layout(
        title='Fronteira Eficiente',
        xaxis_title='Volatilidade Anual',
        yaxis_title='Retorno Anual Esperado',
        showlegend=True,
        hovermode='closest'
    )

    return fig


def detect_price_anomalies(prices, window=20, threshold=2):
    returns = prices.pct_change().dropna()
    model = ARIMA(returns, order=(1,1,1))
    results = model.fit()
    residuals = results.resid
    mean = residuals.rolling(window=window).mean()
    std = residuals.rolling(window=window).std()
    z_scores = (residuals - mean) / std
    return abs(z_scores) > threshold

def calculate_rsi(prices, window=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def optimize_weights(ativos_df):
    def objective(weights):
        # Calcular o score usando os pesos
        scores = calculate_score(ativos_df, weights)
        
        # Calcular a correlação entre os scores e a rentabilidade acumulada
        correlation = np.corrcoef(scores, ativos_df['Rentabilidade Acumulada (5 anos)'])[0, 1]
        
        # Retornar o negativo da correlação (queremos maximizar)
        return -correlation

    # Restrições: soma dos pesos = 1 e todos os pesos >= 0
    constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1},)
    bounds = [(0, 1) for _ in range(7)]  # 7 pesos a serem otimizados

    # Pesos iniciais
    initial_weights = np.array([1/7] * 7)

    # Otimização
    result = minimize(objective, initial_weights, method='SLSQP', bounds=bounds, constraints=constraints)

    return result.x

def calculate_score(ativos_df, weights):
    return (
        weights[0] * ativos_df['ROE'] / ativos_df['P/L'] +
        weights[1] / ativos_df['P/VP'] +
        weights[2] * np.log(ativos_df['Volume']) +
        weights[3] * ativos_df['revenue_growth'] +
        weights[4] * ativos_df['income_growth'] +
        weights[5] * ativos_df['debt_stability'] +
        weights[6] * ativos_df['Dividend Yield']
    )

def calculate_adjusted_score(row, optimized_weights):
    base_score = (
        optimized_weights[0] * row['ROE'] / row['P/L'] +
        optimized_weights[1] / row['P/VP'] +
        optimized_weights[2] * np.log(row['Volume']) +
        optimized_weights[3] * row['revenue_growth'] +
        optimized_weights[4] * row['income_growth'] +
        optimized_weights[5] * row['debt_stability'] +
        optimized_weights[6] * row['Dividend Yield']
    )

    # Fator de qualidade
    quality_factor = (row['ROE'] + row['ROIC']) / 2

    # Aplicação do fator de qualidade
    adjusted_base_score = base_score * (1 + quality_factor * 0.1)

    # Cálculo da penalidade por anomalias
    anomaly_penalty = sum([row[col] for col in ['price_anomaly', 'rsi_anomaly']])

    # Aplicação da penalidade por anomalias
    final_score = adjusted_base_score * (1 - 0.05 * anomaly_penalty)

    return final_score

def adjust_weights_for_growth_and_anomalies(weights, returns, growth_data):
    anomaly_scores = calculate_anomaly_scores(returns)
    growth_scores = growth_data.mean(axis=1)  # Média dos fatores de crescimento
    
    # Normalizar os scores
    growth_scores = (growth_scores - growth_scores.min()) / (growth_scores.max() - growth_scores.min())
    
    # Ajustar pesos
    adjusted_weights = weights * (1 - 0.5 * anomaly_scores + 0.5 * growth_scores)
    return adjusted_weights / adjusted_weights.sum()

def adjust_weights_for_anomalies(weights, anomaly_scores):
    adjusted_weights = weights * (1 - anomaly_scores)
    return adjusted_weights / adjusted_weights.sum()

def calculate_anomaly_scores(returns):
    anomaly_scores = returns.apply(lambda x: detect_price_anomalies(x).mean())
    return anomaly_scores
    
#@st.cache_data(ttl=3600)
def get_financial_growth_data(ticker, years=5):
    stock = yf.Ticker(ticker)
    
    # Obter dados financeiros anuais
    try:
        financials = stock.financials
        balance_sheet = stock.balance_sheet
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        return None
    
    if financials.empty or balance_sheet.empty:
        print(f"No financial data available for {ticker}.")
        return None
    
    try:
        # Verificar se há dados financeiros suficientes
        if 'Total Revenue' not in financials.index or 'Net Income' not in financials.index:
            print(f"Necessary financial metrics not available for {ticker}.")
            return None
        
        # Calcular crescimento da receita
        revenues = financials.loc['Total Revenue'].dropna().sort_index()
        if len(revenues) > 1:
            available_years = min(len(revenues) - 1, years)
            revenue_growth = round((revenues.iloc[-1] / revenues.iloc[-(available_years+1)]) ** (1/available_years) - 1,2)
        else:
            revenue_growth = None
        
        # Calcular crescimento do lucro
        net_income = financials.loc['Net Income'].dropna().sort_index()
        if len(net_income) > 1 and net_income.iloc[0] > 0:
            available_years = min(len(net_income) - 1, years)
            income_growth = round((net_income.iloc[-1] / net_income.iloc[-(available_years+1)]) ** (1/available_years) - 1,2)
        else:
            income_growth = None
        
        # Verificar se há dados de balanço suficientes
        if 'Total Debt' not in balance_sheet.index:
            print(f"Necessary balance sheet metrics not available for {ticker}.")
            return None
        
        # Calcular estabilidade da dívida
        total_debt = balance_sheet.loc['Total Debt'].dropna().sort_index()
        if len(total_debt) > 1:
            available_years = min(len(total_debt) - 1, years)
            debt_stability = round(-((total_debt.iloc[-1] / total_debt.iloc[-(available_years+1)]) ** (1/available_years) - 1),2)
        else:
            debt_stability = None
    except Exception as e:
        print(f"Error calculating growth data for {ticker}: {e}")
        return None
    
    return {
        'revenue_growth': revenue_growth,
        'income_growth': income_growth,
        'debt_stability': debt_stability
    }

def calculate_asset_sharpe(returns_series, risk_free_rate):
    asset_return = returns_series.mean() * 252
    asset_volatility = returns_series.std() * np.sqrt(252)
    return (asset_return - risk_free_rate) / asset_volatility

def generate_allocation_explanation(ticker, allocated_value, shares, fundamental_data, growth_data, anomaly_data, returns, risk_free_rate, portfolio_sharpe):
    ticker = ticker.replace('.SA', '')
    explanation = f"Explicação para a alocação de R$ {allocated_value:.2f} em {ticker}:\n"

    # Calcular Sharpe individual do ativo
    asset_sharpe = calculate_asset_sharpe(returns, risk_free_rate)

    shares = int(shares)

    if shares <= 0:
        explanation = f"Explicação para a não alocação em {ticker}:\n"
        explanation += f"Este ativo apresenta {asset_sharpe:.2f} de índice de sharpe e não foi incluído na alocação final do portfólio otimizado.\n"
        explanation += "Isso pode ocorrer devido a várias razões:\n"
        explanation += "- O ativo pode não contribuir significativamente para a melhoria do índice de Sharpe do portfólio.\n"
        explanation += "- Outros ativos podem oferecer melhor relação risco-retorno ou benefícios de diversificação.\n"
        explanation += "- As restrições de otimização podem ter levado à exclusão deste ativo.\n\n"
    else:
        explanation += f"Índice de Sharpe do ativo: {asset_sharpe:.2f} (Portfolio: {portfolio_sharpe:.2f})\n"
        explanation += "Este ativo foi selecionado principalmente devido à sua contribuição para a otimização do índice de Sharpe do portfólio.\n"

        if asset_sharpe > portfolio_sharpe:
            explanation += "O ativo tem um Sharpe individual superior ao do portfólio, contribuindo positivamente para o desempenho geral.\n"
        else:
            explanation += "Embora o Sharpe individual seja menor que o do portfólio, este ativo ajuda na diversificação e na otimização geral.\n"

    # Adicionar explicações sobre dados fundamentalistas
    explanation += f"\nDados fundamentalistas:"
    explanation += f"\n- P/L: {fundamental_data['P/L']:.2f} "
    explanation += "(favorável) " if fundamental_data['P/L'] < 15 else "(desfavorável) "
    explanation += f"\n- P/VP: {fundamental_data['P/VP']:.2f} "
    explanation += "(favorável) " if fundamental_data['P/VP'] < 1.5 else "(desfavorável) "
    explanation += f"\n- ROE: {fundamental_data['ROE']:.2%} "
    explanation += "(alto) " if fundamental_data['ROE'] > 0.15 else "(baixo) "
    #explanation += f"\n- Dividend Yield: {fundamental_data['Dividend Yield']:.2%} "
    #explanation += "(atrativo) " if fundamental_data['Dividend Yield'] > 0.04 else "(baixo) "

    # Adicionar explicações sobre dados de crescimento
    explanation += f"\n\nDados de crescimento:"
    explanation += f"\n- Crescimento de receita: {growth_data['revenue_growth']:.2%} "
    explanation += "(forte) " if growth_data['revenue_growth'] > 0.1 else "(fraco) "
    explanation += f"\n- Crescimento de lucro: {growth_data['income_growth']:.2%} "
    explanation += "(forte) " if growth_data['income_growth'] > 0.1 else "(fraco) "

    # Adicionar explicações sobre anomalias
    explanation += f"\n\nAnálise de anomalias:"
    explanation += f"\n- Anomalias de preço: {anomaly_data['price_anomaly']:.2%} "
    explanation += "(poucas) " if anomaly_data['price_anomaly'] < 0.1 else "(muitas) "
    explanation += f"\n- Anomalias de RSI: {anomaly_data['rsi_anomaly']:.2%} "
    explanation += "(poucas) " if anomaly_data['rsi_anomaly'] < 0.1 else "(muitas) "

    explanation += "\n\nA alocação final é resultado da otimização do portfólio para maximizar o índice de Sharpe, "
    explanation += "considerando o equilíbrio entre retorno esperado, risco e correlações entre os ativos."

    return explanation

# MongoDB Atlas connection
mongo_uri = st.secrets["mongo_uri"]
client = MongoClient(mongo_uri)
db = client['StockIdea']
collection = db['transactions']
prices_collection = db['historical_prices']

# Function to initialize the database
def init_db():
    if 'transactions' not in db.list_collection_names():
        collection.create_index([('Date', 1), ('Ticker', 1), ('Action', 1), ('Quantity', 1), ('Price', 1)])

# Function to log transactions
def log_transaction(date, ticker, action, quantity, price):
    transaction = {
        'Date': date,
        'Ticker': ticker,
        'Action': action,
        'Quantity': quantity,
        'Price': price
    }
    collection.insert_one(transaction)
    st.success('Transação registrada com sucesso!')

# Function to buy stocks
def buy_stock(date, ticker, quantity, price):
    log_transaction(date, ticker, 'BUY', quantity, price)

# Function to sell stocks
def sell_stock(date, ticker, quantity, price):
    log_transaction(date, ticker, 'SELL', quantity, price)

def get_historical_prices(ticker, start_date, end_date):
    # Sempre buscar dados atualizados do Yahoo Finance
    end_date = datetime.now()  # Usar a data e hora atual
    data = yf.download(ticker, start=start_date, end=end_date)['Adj Close']
    
    # Se não houver dados suficientes, informar o usuário
    if data.empty or data.index[0] > start_date:
        st.warning(f"Dados incompletos para {ticker} de {start_date} a {end_date}.")
    
    return pd.DataFrame({'date': data.index, 'adjusted_close': data.values})

#@st.cache_data(ttl=3600)
# Function to get portfolio performance
def get_portfolio_performance():
    transactions = list(collection.find())
    if not transactions:
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(transactions)
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date')

    portfolio = {}
    invested_value = {}
    for _, row in df.iterrows():
        ticker = row['Ticker']
        if ticker not in portfolio:
            portfolio[ticker] = 0
            invested_value[ticker] = 0
        if row['Action'] == 'BUY':
            portfolio[ticker] += row['Quantity']
            invested_value[ticker] += row['Quantity'] * row['Price']
        else:  # SELL
            sell_ratio = row['Quantity'] / portfolio[ticker]
            portfolio[ticker] -= row['Quantity']
            invested_value[ticker] -= invested_value[ticker] * sell_ratio

    tickers = list(portfolio.keys())
    end_date = datetime.now()  # Sempre usar a data e hora atual
    start_date = df['Date'].min()  # Data da primeira transação

    prices = pd.DataFrame()
    for ticker in tickers:
        ticker_prices = get_historical_prices(ticker, start_date, end_date)
        prices[ticker] = ticker_prices.set_index('date')['adjusted_close']
    
    daily_value = prices.copy()
    for ticker in tickers:
        daily_value[ticker] *= portfolio[ticker]

    return daily_value, pd.Series(invested_value)
    
def get_ibovespa_data(start_date, end_date):
    ibov = get_historical_prices('^BVSP', start_date, end_date)
    ibov_return = (ibov.set_index('date')['adjusted_close'] / ibov.set_index('date')['adjusted_close'].iloc[0] - 1) * 100
    return ibov_return

def calculate_portfolio_metrics(portfolio_data, invested_value):
    total_invested = invested_value.sum()
    current_value = portfolio_data.iloc[-1].sum()
    total_return = ((current_value - total_invested) / total_invested) * 100
    return total_invested, current_value, total_return

def calculate_optimal_contribution(portfolio_data, invested_value, contribution_amount):
    tickers = portfolio_data.columns
    current_weights = portfolio_data.iloc[-1] / portfolio_data.iloc[-1].sum()
    
    returns = portfolio_data.pct_change().dropna()
    
    # Obter dados fundamentalistas e preços atuais
    fundamental_data = {}
    current_prices = {}
    for ticker in tickers:
        fundamental_data[ticker] = get_fundamental_data(ticker)
        current_prices[ticker] = yf.Ticker(ticker).history(period="1d")['Close'].iloc[-1]
    
    def objective(weights):
        portfolio_return = np.sum(returns.mean() * weights) * 252
        portfolio_volatility = np.sqrt(np.dot(weights.T, np.dot(returns.cov() * 252, weights)))
        sharpe_ratio = portfolio_return / portfolio_volatility
        
        quality_score = np.mean([
            fundamental_data[ticker]['ROE'] / fundamental_data[ticker]['P/L'] 
            if fundamental_data[ticker]['P/L'] > 0 else 0
            for ticker in tickers
        ])
        
        diversity_penalty = np.sum(np.square(weights - 1/len(weights)))
        
        return -(sharpe_ratio + quality_score - diversity_penalty)

    constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
    bounds = tuple((0, 1) for _ in range(len(tickers)))
    
    result = minimize(objective, current_weights, method='SLSQP', bounds=bounds, constraints=constraints)
    
    optimal_weights = result.x
    
    # Calcular a contribuição inicial por ativo
    initial_contribution = optimal_weights * contribution_amount
    
    # Ajustar a contribuição para garantir pelo menos uma ação de cada ativo
    final_contribution = {}
    remaining_amount = contribution_amount
    
    for ticker, amount in zip(tickers, initial_contribution):
        price = current_prices[ticker]
        shares = max(1, int(amount / price))  # Garantir pelo menos uma ação
        allocated_amount = shares * price
        
        if allocated_amount <= remaining_amount:
            final_contribution[ticker] = allocated_amount
            remaining_amount -= allocated_amount
        else:
            break  # Se não houver fundos suficientes, pare de alocar
    
    # Distribuir o valor restante, se houver
    while remaining_amount > 0:
        for ticker in final_contribution.keys():
            price = current_prices[ticker]
            if price <= remaining_amount:
                final_contribution[ticker] += price
                remaining_amount -= price
                if remaining_amount < min(current_prices.values()):
                    break
        else:
            break  # Se não foi possível alocar mais, saia do loop
    
    return pd.Series(final_contribution)

def allocate_portfolio_integer_shares(invest_value, prices, weights):
    allocation = {}
    remaining_value = invest_value
    
    # Ordenar os ativos por peso, do maior para o menor
    sorted_assets = sorted(zip(weights, prices.index), reverse=True)
    
    for weight, ticker in sorted_assets:
        price = prices[ticker]
        target_value = invest_value * weight
        shares = int(target_value / price)  # Arredonda para baixo para obter um número inteiro de ações
        
        if shares > 0 and price * shares <= remaining_value:
            allocation[ticker] = shares
            remaining_value -= price * shares
    
    # Tenta alocar o valor restante em mais ações, se possível
    for weight, ticker in sorted_assets:
        price = prices[ticker]
        if price <= remaining_value:
            additional_shares = int(remaining_value / price)
            if additional_shares > 0:
                allocation[ticker] = allocation.get(ticker, 0) + additional_shares
                remaining_value -= price * additional_shares
    
    return allocation, remaining_value

def get_market_data(tickers, start_date, end_date):
    """Obtém dados de mercado para os tickers especificados."""
    data = yf.download(tickers, start=start_date, end=end_date)
    return data['Adj Close']

def get_fundamental_data(tickers):
    """Obtém dados fundamentalistas para os tickers especificados."""
    fundamental_data = {}
    for ticker in tickers:
        stock = yf.Ticker(ticker)
        info = stock.info
        fundamental_data[ticker] = {
            'P/E': info.get('trailingPE', np.nan),
            'P/B': info.get('priceToBook', np.nan),
            'ROE': info.get('returnOnEquity', np.nan),
            'Debt/Equity': info.get('debtToEquity', np.nan),
            'Dividend Yield': info.get('dividendYield', np.nan)
        }
    return pd.DataFrame(fundamental_data).T

def calculate_momentum(returns, window=12):
    """Calcula o momentum dos ativos."""
    return returns.rolling(window=window).mean()

def estimate_future_returns(returns, window=60):
    """Estima retornos futuros usando um modelo ARIMA."""
    future_returns = {}
    for column in returns.columns:
        model = SARIMAX(returns[column], order=(1,1,1), seasonal_order=(1,1,1,12))
        results = model.fit()
        forecast = results.forecast(steps=12)
        future_returns[column] = forecast.mean()
    return pd.Series(future_returns)

def get_sector_data(tickers):
    """Obtém dados de setor para os tickers especificados."""
    sector_data = {}
    for ticker in tickers:
        stock = yf.Ticker(ticker)
        sector_data[ticker] = stock.info.get('sector', 'Unknown')
    return pd.Series(sector_data)

def calculate_transaction_costs(current_weights, new_weights, cost_per_trade=0.001):
    """Calcula os custos de transação para o rebalanceamento."""
    return np.sum(np.abs(new_weights - current_weights)) * cost_per_trade

def black_litterman_returns(market_prices, market_caps, risk_aversion, tau, P, Q):
    """Calcula retornos esperados usando o modelo Black-Litterman."""
    from pypfopt import black_litterman
    delta = black_litterman.market_implied_risk_aversion(market_prices)
    S = risk_models.CovarianceShrinkage(market_prices).ledoit_wolf()
    pi = delta * S.dot(market_caps)
    bl = black_litterman.BlackLittermanModel(S, pi=pi, absolute_views=Q, P=P, tau=tau)
    return bl.bl_returns()

def optimize_portfolio(returns, fundamental_data, momentum, estimated_future_returns, sector_data, current_weights, risk_free_rate, risk_aversion, sector_constraints, style_constraints):
    """Otimiza a alocação da carteira considerando múltiplos fatores."""
    
    # Combinar diferentes fontes de retornos esperados
    expected_returns = 0.3 * returns.mean() + 0.3 * momentum.iloc[-1] + 0.4 * estimated_future_returns
    
    # Ajustar retornos esperados com base em dados fundamentalistas
    fundamental_score = (fundamental_data['ROE'] / fundamental_data['P/E']) * (1 / fundamental_data['Debt/Equity'])
    expected_returns += 0.1 * fundamental_score
    
    # Calcular matriz de covariância usando o método Ledoit-Wolf
    cov_matrix = LedoitWolf().fit(returns).covariance_
    
    # Definir função objetivo
    def objective(weights):
        portfolio_return = np.sum(weights * expected_returns)
        portfolio_volatility = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
        sharpe_ratio = (portfolio_return - risk_free_rate) / portfolio_volatility
        transaction_costs = calculate_transaction_costs(current_weights, weights)
        return -sharpe_ratio + risk_aversion * transaction_costs
    
    # Definir restrições
    constraints = [{'type': 'eq', 'fun': lambda x: np.sum(x) - 1}]  # Soma dos pesos = 1
    
    # Adicionar restrições de setor
    for sector, (min_weight, max_weight) in sector_constraints.items():
        sector_assets = sector_data[sector_data == sector].index
        constraints.append({'type': 'ineq', 'fun': lambda x, sector_assets=sector_assets: np.sum(x[sector_assets]) - min_weight})
        constraints.append({'type': 'ineq', 'fun': lambda x, sector_assets=sector_assets: max_weight - np.sum(x[sector_assets])})
    
    # Adicionar restrições de estilo
    for style, (min_weight, max_weight) in style_constraints.items():
        if style == 'value':
            style_assets = fundamental_data[fundamental_data['P/B'] < fundamental_data['P/B'].median()].index
        elif style == 'growth':
            style_assets = fundamental_data[fundamental_data['ROE'] > fundamental_data['ROE'].median()].index
        elif style == 'dividend':
            style_assets = fundamental_data[fundamental_data['Dividend Yield'] > 0].index
        constraints.append({'type': 'ineq', 'fun': lambda x, style_assets=style_assets: np.sum(x[style_assets]) - min_weight})
        constraints.append({'type': 'ineq', 'fun': lambda x, style_assets=style_assets: max_weight - np.sum(x[style_assets])})
    
    # Otimização
    bounds = tuple((0, 1) for _ in range(len(returns.columns)))
    initial_weights = current_weights if current_weights is not None else np.array([1/len(returns.columns)] * len(returns.columns))
    result = minimize(objective, initial_weights, method='SLSQP', bounds=bounds, constraints=constraints)
    
    return result.x

def rebalance_portfolio(tickers, current_weights, investment_amount, risk_free_rate=0.03, risk_aversion=1, sector_constraints={}, style_constraints={}):
    """Função principal para rebalancear a carteira."""
    
    # Obter dados
    market_data = get_market_data(tickers, start_date='2010-01-01', end_date=datetime.now().strftime('%Y-%m-%d'))
    returns = market_data.pct_change().dropna()
    fundamental_data = get_fundamental_data(tickers)
    momentum = calculate_momentum(returns)
    estimated_future_returns = estimate_future_returns(returns)
    sector_data = get_sector_data(tickers)
    
    # Otimizar carteira
    optimal_weights = optimize_portfolio(returns, fundamental_data, momentum, estimated_future_returns, 
                                         sector_data, current_weights, risk_free_rate, risk_aversion, 
                                         sector_constraints, style_constraints)
    
    # Calcular alocação em reais
    allocation = optimal_weights * investment_amount
    
    # Ajustar para compra de ações inteiras
    prices = market_data.iloc[-1]
    shares = np.floor(allocation / prices)
    actual_allocation = shares * prices
    
    return pd.DataFrame({
        'Ticker': tickers,
        'Optimal Weight': optimal_weights,
        'Allocation (R$)': actual_allocation,
        'Shares': shares
    }).set_index('Ticker')


# New function for portfolio tracking page
def portfolio_tracking():
    st.title('Acompanhamento da Carteira')

    # Initialize database
    init_db()

    # Get portfolio performance
    portfolio_data, invested_value = get_portfolio_performance()
    if not portfolio_data.empty:
        total_invested, current_value, total_return = calculate_portfolio_metrics(portfolio_data, invested_value)

    # Get all assets
    assets_df = load_assets()
    tickers = assets_df['Ticker'].apply(lambda x: x + '.SA').tolist()

    # Transaction input
    st.subheader('Registrar Transação')
    col1, col2, col3 = st.columns(3)
    with col1:
        transaction_date = st.date_input('Data da Transação', value=datetime.now().date())
    with col2:
        transaction_ticker = st.selectbox('Ticker', options=tickers)
    with col3:
        transaction_action = st.selectbox('Ação', options=['Compra', 'Venda'])
    col4, col5 = st.columns(2)
    with col4:
        transaction_quantity = st.number_input('Quantidade', min_value=1, value=1, step=1)
    with col5:
        transaction_price = st.number_input('Preço', min_value=0.01, value=1.00, step=0.01)

    if st.button('Registrar Transação'):
        transaction_date_str = transaction_date.strftime('%Y-%m-%d %H:%M:%S')
        if transaction_action == 'Compra':
            buy_stock(transaction_date_str, transaction_ticker, transaction_quantity, transaction_price)
        else:
            sell_stock(transaction_date_str, transaction_ticker, transaction_quantity, transaction_price)

    # Display portfolio performance
    st.subheader('Desempenho da Carteira')
    portfolio_data, invested_value = get_portfolio_performance()
    if not portfolio_data.empty:
        total_invested, current_value, total_return = calculate_portfolio_metrics(portfolio_data, invested_value)
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Valor Total Investido", f"R$ {total_invested:.2f}")
        col2.metric("Valor Atual da Carteira", f"R$ {current_value:.2f}")
        col3.metric("Retorno Total", f"{total_return:.2f}%")

        # Calculate returns for each asset
        asset_returns = {}
        for ticker in portfolio_data.columns:
            initial_value = invested_value[ticker]
            current_value = portfolio_data[ticker].iloc[-1]
            if initial_value > 0:
                asset_return = ((current_value - initial_value) / initial_value) * 100
                asset_returns[ticker] = {
                    'return': asset_return,
                    'current_value': current_value
                }

        # Sort assets by return
        sorted_assets = sorted(asset_returns.items(), key=lambda x: x[1]['return'], reverse=True)

        # Create bar chart for asset returns
        fig_asset_returns = go.Figure()
        tickers = []
        returns = []
        current_values = []
        for ticker, data in sorted_assets:
            tickers.append(ticker)
            returns.append(data['return'])
            current_values.append(data['current_value'])

        fig_asset_returns.add_trace(go.Bar(
            x=tickers,
            y=returns,
            text=[f"{r:.2f}%<br>R$ {v:.2f}" for r, v in zip(returns, current_values)],
            textposition='auto',
            name='Retorno Acumulado'
        ))

        fig_asset_returns.update_layout(
            title='Retorno Acumulado por Ativo',
            xaxis_title='Ativo',
            yaxis_title='Retorno Acumulado (%)',
            yaxis_tickformat = '.2f%'
        )

        st.plotly_chart(fig_asset_returns)

         # Calculate daily portfolio value
        daily_portfolio_value = portfolio_data.sum(axis=1)

        # Calculate daily returns
        daily_returns = daily_portfolio_value.pct_change()

        # Calculate cumulative returns
        portfolio_cumulative_returns = (1 + daily_returns).cumprod() - 1
        portfolio_cumulative_returns = portfolio_cumulative_returns * 100  # Convert to percentage

        # Ensure the final return matches the total return
        portfolio_cumulative_returns = portfolio_cumulative_returns * (total_return / portfolio_cumulative_returns.iloc[-1])

        # Get Ibovespa data
        ibov_return = get_ibovespa_data(portfolio_data.index[0], portfolio_data.index[-1])

        # Create figure for cumulative returns comparison
        fig_returns = go.Figure()
        fig_returns.add_trace(go.Scatter(x=portfolio_cumulative_returns.index, y=portfolio_cumulative_returns.values, 
                                         mode='lines', name='Carteira',
                                         hovertemplate='Data: %{x}<br>Retorno Carteira: %{y:.2f}%'))
        fig_returns.add_trace(go.Scatter(x=ibov_return.index, y=ibov_return.values, 
                                         mode='lines', name='Ibovespa',
                                         hovertemplate='Data: %{x}<br>Retorno Ibovespa: %{y:.2f}%'))
        fig_returns.update_layout(
            title='Comparação de Retorno Percentual Acumulado: Carteira vs Ibovespa',
            xaxis_title='Data',
            yaxis_title='Retorno Acumulado (%)',
            yaxis_tickformat = '.2f%',
            hovermode='x unified'
        )
        st.plotly_chart(fig_returns)   


    else:
        st.write("Não há transações registradas ainda.")

    st.subheader('Aporte Inteligente na Carteira')
    contribution_amount = st.number_input('Valor do Aporte (R$)', min_value=0.01, value=1000.00, step=0.01)

    if st.button('Rebalancear Carteira'):
        tickers = list(portfolio_data.keys())
        current_weights = np.array([portfolio_data[ticker] / sum(portfolio_data.values()) for ticker in tickers])
        
        # Rebalancear a carteira com a função otimizada
        result = rebalance_portfolio(tickers, current_weights, investment_amount)
        
        st.write("Novo rebalanceamento de carteira:")
        st.dataframe(result)


    if st.button('Calcular Distribuição Ótima do Aporte'):
        portfolio_data, invested_value = get_portfolio_performance()
        if not portfolio_data.empty:
            optimal_contribution = calculate_optimal_contribution(portfolio_data, invested_value, contribution_amount)
        
            st.write("Distribuição Ótima do Aporte:")
            contribution_df = pd.DataFrame({
                'Ativo': optimal_contribution.index,
                'Valor do Aporte': optimal_contribution.values,
                'Quantidade de Ações': (optimal_contribution / [yf.Ticker(ticker).history(period="1d")['Close'].iloc[-1] for ticker in optimal_contribution.index]).astype(int),
                'Porcentagem do Aporte': optimal_contribution / contribution_amount * 100
            })
            contribution_df = contribution_df.sort_values('Valor do Aporte', ascending=False)
            contribution_df['Valor do Aporte'] = contribution_df['Valor do Aporte'].map('R$ {:.2f}'.format)
            contribution_df['Porcentagem do Aporte'] = contribution_df['Porcentagem do Aporte'].map('{:.2f}%'.format)
        
            st.table(contribution_df)
            
            
            # Explicação da estratégia
            st.subheader("Explicação da Estratégia de Aporte")
            st.write("""
            A estratégia de aporte utiliza uma abordagem multifatorial para determinar a alocação ótima:
            
            1. Desempenho Histórico: Considera o retorno histórico e a volatilidade dos ativos.
            2. Análise Fundamentalista: Incorpora métricas como P/L, ROE e Dividend Yield.
            3. Diversificação: Busca manter um portfólio bem diversificado.
            4. Qualidade dos Ativos: Prioriza ativos com bons fundamentos.
            
            Esta abordagem visa equilibrar o risco e o retorno, considerando tanto o desempenho passado quanto a saúde financeira atual das empresas.
            """)
        else:
            st.write("Não há dados suficientes para calcular a distribuição do aporte.")


def main():
    st.sidebar.title('Navegação')
    page = st.sidebar.radio('Selecione uma página', ['Recomendação de Ativos', 'Acompanhamento da Carteira'])

    if page == 'Recomendação de Ativos':

        ativos_df = load_assets()
        ativos_df = ativos_df.dropna(subset=['Type'])
        
        #ativos_df= ativos_df[ativos_df['Ticker'].str.contains('34')]
    
        # Substituir "-" por "Outros" na coluna "Sector"
        ativos_df["Sector"] = ativos_df["Sector"].replace("-", "Outros")
    
        setores = sorted(set(ativos_df['Sector']))
        setores.insert(0, 'Todos')
    
        sector_filter = st.multiselect('Selecione o Setor', options=setores)

        tipo = sorted(set(ativos_df['Type']))
        tipo.insert(0, 'Todos')
    
        type_filter = st.multiselect('Selecione o tipo de ativo', options=tipo)
    
        if 'Todos' not in sector_filter or 'Todos' not in type_filter:
            if 'Todos' not in sector_filter:
                ativos_df = ativos_df[ativos_df['Sector'].isin(sector_filter)]
            if 'Todos' not in type_filter:
                ativos_df = ativos_df[ativos_df['Type'].isin(type_filter)]
    
        invest_value = st.number_input('Valor a ser investido (R$)', min_value=100.0, value=10000.0, step=100.0)
    
        if st.button('Gerar Recomendação'):
            progress_bar = st.progress(0)
            status_text = st.empty()
    
            # Obter dados fundamentalistas
            fundamental_data = []
            for i, ticker in enumerate(ativos_df['Ticker']):
                status_text.text(f'Carregando dados para {ticker}...')
                progress_bar.progress((i + 1) / len(ativos_df))
                data = get_fundamental_data(ticker + '.SA')
                growth_data = get_financial_growth_data(ticker + '.SA')
                if growth_data:
                    data.update(growth_data)
                data['Ticker'] = ticker
                fundamental_data.append(data)

    
            fundamental_df = pd.DataFrame(fundamental_data)
            ativos_df = ativos_df.merge(fundamental_df, on='Ticker')
    
            # Filtrar ativos com informações necessárias
            ativos_df = ativos_df.dropna(subset=['P/L', 'P/VP', 'ROE', 'ROIC', 'Dividend Yield','Volume', 'Price', 'revenue_growth', 'income_growth', 'debt_stability'])
      
            
         
            #Filtrar ativos com boa liquidez
            #ativos_df = ativos_df[ativos_df.Volume > ativos_df.Volume.quantile(.25)]
    
            # Verificar se há ativos suficientes para continuar
            if len(ativos_df) < 10:
                st.error("Não há ativos suficientes com dados completos para realizar a análise. Por favor, tente novamente mais tarde.")
                return
    
            # Análise fundamentalista e de liquidez
            ativos_df['Score'] = (
                ativos_df['ROE'] / ativos_df['P/L'] +
                1 / ativos_df['P/VP'] +
                np.log(ativos_df['Volume'])
            )
    
            tickers_raw = ativos_df['Ticker'].apply(lambda x: x + '.SA').tolist()
            
            stock_data_raw = get_stock_data(tickers_raw)
    
            # Detecção de anomalias e cálculo de RSI
            for ticker in tickers_raw:
                price_anomalies = detect_price_anomalies(stock_data_raw[ticker])
                rsi = calculate_rsi(stock_data_raw[ticker])
                ativos_df.loc[ativos_df['Ticker'] == ticker[:-3], 'price_anomaly'] = price_anomalies.mean()
                ativos_df.loc[ativos_df['Ticker'] == ticker[:-3], 'rsi_anomaly'] = (rsi > 70).mean() + (rsi < 30).mean()
    
            # Calcular score ajustado
            cumulative_returns_raw = [get_cumulative_return(ticker) for ticker in tickers_raw]
            ativos_df['Rentabilidade Acumulada (5 anos)'] = cumulative_returns_raw
            optimized_weights = optimize_weights(ativos_df)
            ativos_df['Adjusted_Score'] = ativos_df.apply(lambda row: calculate_adjusted_score(row, optimized_weights), axis=1)
    
            # Selecionar os top 10 ativos com base no score
            top_ativos = ativos_df.nlargest(10, 'Adjusted_Score')
            growth_data = top_ativos[['revenue_growth', 'income_growth']].mean(axis=1).values
            quality_data = top_ativos['ROIC'].values

    
            tickers = top_ativos['Ticker'].apply(lambda x: x + '.SA').tolist()
            status_text.text('Obtendo dados históricos...')
            stock_data = get_stock_data(tickers)
    
            # Verificar se os dados históricos foram obtidos com sucesso
            if stock_data.empty:
                st.error("Não foi possível obter dados históricos. Por favor, tente novamente mais tarde.")
                return
    
    
            st.subheader('Top 10 BDRs Recomendados')
            st.dataframe(top_ativos[['Ticker', 'Sector', 'P/L', 'P/VP', 'ROE', 'ROIC', 'Dividend Yield','Volume', 'Price', 'Score', 'Adjusted_Score','revenue_growth','income_growth','debt_stability','Rentabilidade Acumulada (5 anos)']])
    
            # Otimização de portfólio
            returns = calculate_returns(stock_data)
    
            # Verificar se há retornos válidos para continuar
            if returns.empty:
                st.error("Não foi possível calcular os retornos dos ativos. Por favor, tente novamente mais tarde.")
                return
    
            # Calcular rentabilidade acumulada
            cumulative_returns = [get_cumulative_return(ticker) for ticker in tickers]
            top_ativos['Rentabilidade Acumulada (5 anos)'] = cumulative_returns
    
            # Otimização de portfólio
            returns = calculate_returns(stock_data)
    
            # Verificar se há retornos válidos para continuar
            if returns.empty:
                st.error("Não foi possível calcular os retornos dos ativos. Por favor, tente novamente mais tarde.")
                return
    
            global risk_free_rate
            risk_free_rate = 0.1
    
            status_text.text('Otimizando portfólio...')
            try:
                optimal_weights = optimize_portfolio(returns, risk_free_rate)
                # Ajustar pesos com base nas anomalias
                anomaly_scores = calculate_anomaly_scores(returns)
                adjusted_weights = adjust_weights_for_anomalies(optimal_weights, anomaly_scores)
            except Exception as e:
                st.error(f"Erro ao otimizar o portfólio: {e}")
                return


            # Exibir informações sobre anomalias detectadas
            st.subheader('Análise de Anomalias')
            anomaly_data = []
            for ticker in tickers:
                price_anomalies = detect_price_anomalies(stock_data[ticker])
                rsi = calculate_rsi(stock_data[ticker])
                rsi_anomalies = (rsi > 70) | (rsi < 30)
                anomaly_data.append({
                    'Ticker': ticker[:-3],
                    'price_anomaly': round(price_anomalies.mean(),2),
                    'rsi_anomaly': round(rsi_anomalies.mean(),2)
                })
            
            anomaly_df = pd.DataFrame(anomaly_data)
            st.table(anomaly_df)
    
            st.write("As anomalias de preço indicam movimento incomuns nos preços dos atios, enquanto as anomalias de RSI indicam períodos de sobrecompra ou sobrevenda.")

            portfolio_return, portfolio_volatility = portfolio_performance(adjusted_weights, returns)
            portfolio_sharpe = (portfolio_return - risk_free_rate) / portfolio_volatility

            prices = top_ativos.set_index('Ticker')['Price']
            allocation, remaining_value = allocate_portfolio_integer_shares(invest_value, prices, adjusted_weights)
            

            st.subheader('Alocação Ótima do Portfólio')
            allocation_data = []
            for ticker, shares in allocation.items():
                price = prices[ticker]  # Remove o '.SA' do ticker
                allocated_value = shares * price
                cumulative_return = top_ativos.loc[top_ativos['Ticker'] == ticker, 'Rentabilidade Acumulada (5 anos)'].values[0]
                
                # Obter dados para explicação
                fundamental_data = top_ativos.loc[top_ativos['Ticker'] == ticker, ['P/L', 'P/VP', 'ROE']].to_dict('records')[0]
                growth_data = top_ativos.loc[top_ativos['Ticker'] == ticker, ['revenue_growth', 'income_growth']].to_dict('records')[0]
                anomaly_data = anomaly_df.loc[anomaly_df['Ticker'] == ticker, ['price_anomaly', 'rsi_anomaly']].to_dict('records')[0]

                ticr = f"{ticker}.SA"
                
                explanation = generate_allocation_explanation(ticr, allocated_value, shares, fundamental_data, growth_data, anomaly_data, returns[ticr], risk_free_rate, portfolio_sharpe)

                allocation_data.append({
                    'Ticker': ticker,
                    'Quantidade de Ações': f"{shares}",
                    'Valor Alocado': f"R$ {allocated_value:.2f}",
                    'Rentabilidade Acumulada (5 anos)': f"{cumulative_return:.2%}" if cumulative_return is not None else "N/A",
                    'Explicação': explanation
                })

            allocation_df = pd.DataFrame(allocation_data)
            st.table(allocation_df[['Ticker', 'Quantidade de Ações', 'Valor Alocado', 'Rentabilidade Acumulada (5 anos)']])

            st.write(f"Valor não investido: R$ {remaining_value:.2f}")
            
            # Exibir explicações
            for _, row in allocation_df.iterrows():
                with st.expander(f"Explicação para {row['Ticker']}"):
                    st.write(row['Explicação'])
    
            portfolio_return, portfolio_volatility = portfolio_performance(adjusted_weights, returns)
            sharpe_ratio = (portfolio_return - risk_free_rate) / portfolio_volatility
    
            st.subheader('Métricas do Portfólio')
            st.write(f"Retorno Anual Esperado: {portfolio_return:.2%}")
            st.write(f"Volatilidade Anual: {portfolio_volatility:.2%}")
            st.write(f"Índice de Sharpe: {sharpe_ratio:.2f}")
    
            # Gerar e exibir o gráfico de dispersão
            status_text.text('Gerando gráfico da fronteira eficiente...')
            fig = plot_efficient_frontier(returns, adjusted_weights)
            st.plotly_chart(fig)
    
    
            status_text.text('Análise concluída!')
            progress_bar.progress(100)
            pass
         
    
    elif page == 'Acompanhamento da Carteira':
        portfolio_tracking()

if __name__ == "__main__":
    main()
