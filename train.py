import os
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset
import cv2
import matplotlib.pyplot as plt

# LSTM + Transformer模型定义
class VideoLSTMTransformerModel(nn.Module):
    def __init__(self, input_size, hidden_size, num_classes):
        super(VideoLSTMTransformerModel, self).__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.transformer = nn.Transformer(d_model=hidden_size, nhead=8, num_encoder_layers=6, num_decoder_layers=6,
                                          batch_first=True)
        self.fc = nn.Linear(hidden_size, num_classes)

    def forward(self, x):
        batch_size, seq_len, c, h, w = x.size()
        x = x.reshape(batch_size, seq_len, -1)  # [batch_size, seq_len, c*h*w]
        lstm_out, _ = self.lstm(x)  # LSTM output
        transformer_out = self.transformer(lstm_out, lstm_out)  # Transformer for sequence modeling
        out = self.fc(transformer_out[:, -1, :])  # Use the last output for classification
        return out


# 数据预处理与加载
class VideoDataset(Dataset):
    def __init__(self, video_paths, labels, fixed_frame_count=100, transform=None):
        self.video_paths = video_paths
        self.labels = labels
        self.fixed_frame_count = fixed_frame_count
        self.transform = transform

    def __len__(self):
        return len(self.video_paths)

    def __getitem__(self, idx):
        video_path = self.video_paths[idx]
        label = self.labels[idx]
        cap = cv2.VideoCapture(video_path)
        frames = []
        while cap.isOpened():
            ret, frame = cap.read()
            if ret:
                frame = cv2.resize(frame, (224, 224))
                frames.append(frame)
            else:
                break
        cap.release()
        frames = np.array(frames)
        frames = torch.tensor(frames, dtype=torch.float32) / 255.0

        if len(frames) < self.fixed_frame_count:
            padding = self.fixed_frame_count - len(frames)
            frames = np.pad(frames, ((0, padding), (0, 0), (0, 0), (0, 0)), mode='constant', constant_values=0)
        elif len(frames) > self.fixed_frame_count:
            frames = frames[:self.fixed_frame_count]
        frames = torch.tensor(frames, dtype=torch.float32)
        frames = frames.permute(0, 3, 1, 2)  # 改变维度顺序为 [seq_len, c, h, w]
        return frames, label


class VideoBehaviorModel:
    def __init__(self, video_folder, input_size=224, hidden_size=512, num_classes=101, batch_size=8,
                 epochs=3, fixed_frame_count=100, device='cpu'):
        self.video_folder = video_folder
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_classes = num_classes
        self.batch_size = batch_size
        self.epochs = epochs
        self.fixed_frame_count = fixed_frame_count
        self.device = device

        self.train_loader = None
        self.test_loader = None
        self.model = None

    # 数据准备函数
    def prepare_data(self):
        # 准备训练和测试数据的列表
        train_paths, test_paths, train_labels, test_labels = [], [], [], []

        # 获取所有子文件夹（每个文件夹代表一个类）
        class_names = [folder for folder in os.listdir(self.video_folder) if
                       os.path.isdir(os.path.join(self.video_folder, folder))]
        class_names.sort()  # 确保类名按顺序排列，方便标签生成

        # 为每个类分配一个整数标签
        label_dict = {class_name: idx for idx, class_name in enumerate(class_names)}

        for video_class, label in label_dict.items():
            class_path = os.path.join(self.video_folder, video_class)
            for video_name in os.listdir(class_path):
                if video_name.endswith('.avi'):
                    video_path = os.path.join(class_path, video_name)
                    # 按80%训练集，20%测试集
                    if np.random.rand() < 0.8:
                        train_paths.append(video_path)
                        train_labels.append(label)
                    else:
                        test_paths.append(video_path)
                        test_labels.append(label)

        print(f"Training samples: {len(train_paths)}")
        print(f"Testing samples: {len(test_paths)}")

        return train_paths, test_paths, train_labels, test_labels

    # 创建训练和测试DataLoader
    def create_data_loaders(self, train_paths, test_paths, train_labels, test_labels):
        train_dataset = VideoDataset(train_paths, train_labels, fixed_frame_count=self.fixed_frame_count)
        test_dataset = VideoDataset(test_paths, test_labels, fixed_frame_count=self.fixed_frame_count)

        train_loader = DataLoader(train_dataset, batch_size=self.batch_size, shuffle=True, num_workers=16)
        test_loader = DataLoader(test_dataset, batch_size=self.batch_size, shuffle=False, num_workers=16)

        return train_loader, test_loader

    # 模型训练
    def train_model(self, model, train_loader, criterion, optimizer):
        model.to(self.device)
        train_losses, train_accuracies = [], []

        for epoch in range(self.epochs):
            model.train()
            running_loss = 0.0
            correct = 0
            total = 0
            for inputs, labels in train_loader:
                inputs, labels = inputs.to(self.device), labels.to(self.device)
                optimizer.zero_grad()

                outputs = model(inputs)
                loss = criterion(outputs, labels)
                loss.backward()
                optimizer.step()

                running_loss += loss.item()
                _, predicted = torch.max(outputs, 1)
                total += labels.size(0)
                correct += (predicted == labels).sum().item()

            accuracy = 100 * correct / total
            train_losses.append(running_loss / len(train_loader))
            train_accuracies.append(accuracy)

            print(
                f"Epoch {epoch + 1}/{self.epochs}, Loss: {running_loss / len(train_loader):.4f}, Accuracy: {accuracy:.2f}%")

        return train_losses, train_accuracies

    # 测试模型
    def test_model(self, model, test_loader):
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for inputs, labels in test_loader:
                inputs, labels = inputs.to(self.device), labels.to(self.device)
                outputs = model(inputs)
                _, predicted = torch.max(outputs, 1)
                total += labels.size(0)
                correct += (predicted == labels).sum().item()

        accuracy = 100 * correct / total
        print(f"Test Accuracy: {accuracy:.2f}%")
        return accuracy

    # 模型保存
    def save_model(self, model, path='video_behavior_model.pth'):
        torch.save(model.state_dict(), path)
        print(f"Model saved to {path}")

    # 绘制训练结果图表
    def plot_results(self, train_losses, train_accuracies):
        epochs = range(1, self.epochs + 1)
        plt.figure(figsize=(12, 6))

        plt.subplot(1, 2, 1)
        plt.plot(epochs, train_losses, label='Loss')
        plt.title('Training Loss')
        plt.xlabel('Epoch')
        plt.ylabel('Loss')

        plt.subplot(1, 2, 2)
        plt.plot(epochs, train_accuracies, label='Accuracy')
        plt.title('Training Accuracy')
        plt.xlabel('Epoch')
        plt.ylabel('Accuracy (%)')

        plt.tight_layout()
        plt.show()

    # 主训练过程
    def main(self):
        train_paths, test_paths, train_labels, test_labels = self.prepare_data()
        train_loader, test_loader = self.create_data_loaders(train_paths, test_paths, train_labels, test_labels)

        # 使用外部定义的 VideoLSTMTransformerModel 来创建模型
        model = VideoLSTMTransformerModel(input_size=self.input_size, hidden_size=self.hidden_size,
                                          num_classes=self.num_classes)

        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters(), lr=0.001)

        # 训练模型
        train_losses, train_accuracies = self.train_model(model, train_loader, criterion, optimizer)

        # 测试模型
        self.test_model(model, test_loader)

        # 保存模型
        self.save_model(model)

        # 绘制训练过程的图表
        self.plot_results(train_losses, train_accuracies)


if __name__ == '__main__':
    video_folder = 'D:\\ai\\行为预测\\UCF-101'  # 替换为你的UCF101数据集路径
    model = VideoBehaviorModel(video_folder, device='cpu')
    model.main()
